"""
CLOSE governance. Backend/database-backed, same trust model as staking -
no smart contract, no on-chain execution. Proposals are signal-only:
outcomes are recorded here and acted on manually. Voting power comes
exclusively from active stake_positions, snapshotted at the moment a
proposal is created so later staking/unstaking can't change anyone's
weight on a proposal already in progress.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from app.core.database import get_db

logger = logging.getLogger(__name__)

MIN_STAKED_TO_PROPOSE = 100_000_000
VOTING_PERIOD_DAYS = 7
QUORUM_PERCENT = 10  # % of total staked supply (at snapshot time) that must vote


def _get_active_stakes_by_user() -> dict:
    """Returns {user_id: total_active_staked_amount} for every user with
    at least one active stake position."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT user_id, SUM(amount)
                FROM stake_positions
                WHERE status = 'active'
                GROUP BY user_id
            """)
            return {row[0]: row[1] for row in c.fetchall()}


def create_proposal(user_id: str, title: str, description: str) -> dict:
    if not title.strip():
        raise ValueError("Title is required")
    if not description.strip():
        raise ValueError("Description is required")

    stakes_by_user = _get_active_stakes_by_user()
    proposer_stake = stakes_by_user.get(user_id, 0)
    if proposer_stake < MIN_STAKED_TO_PROPOSE:
        raise ValueError(
            f"You need at least {MIN_STAKED_TO_PROPOSE:,} CLOSE staked to create a proposal "
            f"(you have {proposer_stake:,})"
        )

    total_staked = sum(stakes_by_user.values())
    proposal_id = str(uuid.uuid4())
    voting_ends_at = datetime.now(timezone.utc) + timedelta(days=VOTING_PERIOD_DAYS)

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO governance_proposals
                    (id, proposer_id, title, description, voting_ends_at, total_staked_snapshot, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'active')
            """, (proposal_id, user_id, title.strip(), description.strip(), voting_ends_at, total_staked))

            # Snapshot every currently-staked wallet's weight for this
            # proposal specifically. This is what makes voting power
            # immune to stake/unstake activity that happens after this
            # point - votes always check against this table, never
            # against live stake_positions.
            snapshot_rows = [
                (proposal_id, uid, amount)
                for uid, amount in stakes_by_user.items()
            ]
            if snapshot_rows:
                c.executemany("""
                    INSERT INTO governance_vote_weights (proposal_id, user_id, staked_amount)
                    VALUES (%s, %s, %s)
                """, snapshot_rows)

            conn.commit()

    logger.info(f"Proposal created: id={proposal_id} proposer={user_id} total_staked_snapshot={total_staked}")
    return {
        "id": proposal_id,
        "title": title.strip(),
        "voting_ends_at": voting_ends_at.isoformat(),
        "total_staked_snapshot": total_staked,
    }


def vote(user_id: str, proposal_id: str, support: str) -> dict:
    if support not in ("for", "against", "abstain"):
        raise ValueError("support must be 'for', 'against', or 'abstain'")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT voting_ends_at, status FROM governance_proposals WHERE id = %s
            """, (proposal_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Proposal not found")
            voting_ends_at, status = row

            voting_ends_at_aware = voting_ends_at.replace(tzinfo=timezone.utc) if voting_ends_at.tzinfo is None else voting_ends_at
            if datetime.now(timezone.utc) > voting_ends_at_aware:
                raise ValueError("Voting has closed on this proposal")

            c.execute("""
                SELECT staked_amount FROM governance_vote_weights
                WHERE proposal_id = %s AND user_id = %s
            """, (proposal_id, user_id))
            weight_row = c.fetchone()
            if not weight_row or weight_row[0] <= 0:
                raise ValueError("You had no active staked CLOSE when this proposal was created, so you can't vote on it")
            weight = weight_row[0]

            c.execute("""
                SELECT 1 FROM governance_votes WHERE proposal_id = %s AND user_id = %s
            """, (proposal_id, user_id))
            if c.fetchone():
                raise ValueError("You've already voted on this proposal")

            c.execute("""
                INSERT INTO governance_votes (proposal_id, user_id, support, weight)
                VALUES (%s, %s, %s, %s)
            """, (proposal_id, user_id, support, weight))
            conn.commit()

    logger.info(f"Vote cast: proposal={proposal_id} user={user_id} support={support} weight={weight}")
    return {"proposal_id": proposal_id, "support": support, "weight": weight}


def set_founder_decision(proposal_id: str, decision: str, reason: str) -> dict:
    """Founder override/ratification. Only callable after voting has
    closed (can't override a still-active vote) - decision is 'approved'
    or 'rejected', and always requires a public reason, since the
    override is shown on the proposal for every user to see."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not reason.strip():
        raise ValueError("A reason is required for a founder decision")

    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT voting_ends_at FROM governance_proposals WHERE id = %s
            """, (proposal_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Proposal not found")
            voting_ends_at = row[0]

            voting_ends_at_aware = voting_ends_at.replace(tzinfo=timezone.utc) if voting_ends_at.tzinfo is None else voting_ends_at
            if datetime.now(timezone.utc) <= voting_ends_at_aware:
                raise ValueError("Voting is still active on this proposal - a founder decision can only be made after voting closes")

            c.execute("""
                UPDATE governance_proposals
                SET founder_decision = %s, founder_reason = %s, founder_decided_at = NOW()
                WHERE id = %s
            """, (decision, reason.strip(), proposal_id))
            conn.commit()

    logger.info(f"Founder decision: proposal={proposal_id} decision={decision} reason={reason.strip()!r}")
    return {"proposal_id": proposal_id, "founder_decision": decision, "founder_reason": reason.strip()}


def _compute_status(voting_ends_at, total_staked_snapshot, vote_totals) -> str:
    """vote_totals is {"for": x, "against": y, "abstain": z}"""
    voting_ends_at_aware = voting_ends_at.replace(tzinfo=timezone.utc) if voting_ends_at.tzinfo is None else voting_ends_at
    if datetime.now(timezone.utc) <= voting_ends_at_aware:
        return "active"

    total_votes = vote_totals["for"] + vote_totals["against"] + vote_totals["abstain"]
    quorum_required = (total_staked_snapshot * QUORUM_PERCENT) / 100

    if total_votes < quorum_required:
        return "quorum_not_reached"
    return "passed" if vote_totals["for"] > vote_totals["against"] else "failed"


def get_proposal(proposal_id: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, proposer_id, title, description, created_at, voting_ends_at, total_staked_snapshot,
                       founder_decision, founder_reason, founder_decided_at
                FROM governance_proposals WHERE id = %s
            """, (proposal_id,))
            row = c.fetchone()
            if not row:
                raise ValueError("Proposal not found")
            (pid, proposer_id, title, description, created_at, voting_ends_at, total_staked_snapshot,
             founder_decision, founder_reason, founder_decided_at) = row

            c.execute("""
                SELECT support, COALESCE(SUM(weight), 0)
                FROM governance_votes WHERE proposal_id = %s GROUP BY support
            """, (proposal_id,))
            vote_totals = {"for": 0, "against": 0, "abstain": 0}
            for support, total in c.fetchall():
                vote_totals[support] = total

            c.execute("SELECT COUNT(*) FROM governance_votes WHERE proposal_id = %s", (proposal_id,))
            voter_count = c.fetchone()[0]

    status = _compute_status(voting_ends_at, total_staked_snapshot, vote_totals)
    quorum_required = (total_staked_snapshot * QUORUM_PERCENT) / 100

    return {
        "id": pid,
        "proposer_id": proposer_id,
        "title": title,
        "description": description,
        "created_at": created_at.isoformat(),
        "voting_ends_at": voting_ends_at.isoformat(),
        "total_staked_snapshot": total_staked_snapshot,
        "quorum_required": quorum_required,
        "vote_totals": vote_totals,
        "voter_count": voter_count,
        "status": status,
        "founder_decision": founder_decision,
        "founder_reason": founder_reason,
        "founder_decided_at": founder_decided_at.isoformat() if founder_decided_at else None,
        # What actually governs the outcome: the founder's call overrides
        # the raw community vote when present, but the community status
        # is never hidden - both are always returned above.
        "effective_status": founder_decision if founder_decision else status,
    }


def list_proposals() -> list:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM governance_proposals ORDER BY created_at DESC")
            ids = [row[0] for row in c.fetchall()]
    return [get_proposal(pid) for pid in ids]


def get_my_voting_power(user_id: str, proposal_id: str) -> dict:
    """Lets the frontend show 'you can/can't vote, here's your weight'
    before the user commits to a vote() call."""
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT staked_amount FROM governance_vote_weights
                WHERE proposal_id = %s AND user_id = %s
            """, (proposal_id, user_id))
            weight_row = c.fetchone()

            c.execute("""
                SELECT support FROM governance_votes WHERE proposal_id = %s AND user_id = %s
            """, (proposal_id, user_id))
            voted_row = c.fetchone()

    return {
        "eligible": bool(weight_row and weight_row[0] > 0),
        "weight": weight_row[0] if weight_row else 0,
        "already_voted": bool(voted_row),
        "voted_support": voted_row[0] if voted_row else None,
    }

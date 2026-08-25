// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IStakingPool {
    function balanceAtBlock(address account, uint256 blockNumber) external view returns (uint256);
    function totalStakedAtBlock(uint256 blockNumber) external view returns (uint256);
    function currentStakedBalance(address account) external view returns (uint256);
}

/// @title Governance
/// @notice Signal-only voting for CLOSE stakers. Proposals do not execute
///         anything on-chain - this contract only records outcomes.
///         Voting weight for a proposal is the voter's staked balance at
///         the block the proposal was created (read from StakingPool's
///         checkpoint history), so staking or unstaking after a proposal
///         goes live cannot change anyone's weight on it.
///         Immutable: no owner, no pause, no upgrade path.
contract Governance {
    IStakingPool public immutable stakingPool;

    uint256 public constant PROPOSAL_THRESHOLD = 10_000_000 * 1e18; // min staked CLOSE to propose
    uint256 public constant MIN_VOTING_PERIOD = 3 days;
    uint256 public constant MAX_VOTING_PERIOD = 30 days;
    uint256 public constant QUORUM_BPS = 1000; // 10.00% of total staked supply, in basis points (out of 10_000)

    enum VoteType { Against, For, Abstain }
    enum ProposalState { Active, Defeated, Succeeded, QuorumNotReached }

    struct Proposal {
        address proposer;
        string description;
        uint256 startBlock;      // block at which voting weight is snapshotted
        uint256 endTime;         // timestamp voting closes
        uint256 forVotes;
        uint256 againstVotes;
        uint256 abstainVotes;
    }

    Proposal[] public proposals;

    // proposalId => voter => has voted
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    event ProposalCreated(
        uint256 indexed proposalId,
        address indexed proposer,
        string description,
        uint256 startBlock,
        uint256 endTime
    );
    event VoteCast(
        uint256 indexed proposalId,
        address indexed voter,
        VoteType support,
        uint256 weight
    );

    constructor(address _stakingPool) {
        require(_stakingPool != address(0), "zero staking pool address");
        stakingPool = IStakingPool(_stakingPool);
    }

    // ---------------------------------------------------------------------
    // Proposal creation
    // ---------------------------------------------------------------------

    /// @param description Free-text description of what's being signaled on.
    /// @param votingPeriod How long voting stays open, in seconds. Must be
    ///        between MIN_VOTING_PERIOD and MAX_VOTING_PERIOD.
    function propose(string calldata description, uint256 votingPeriod) external returns (uint256) {
        require(bytes(description).length > 0, "description required");
        require(votingPeriod >= MIN_VOTING_PERIOD, "voting period too short");
        require(votingPeriod <= MAX_VOTING_PERIOD, "voting period too long");

        uint256 proposerStake = stakingPool.currentStakedBalance(msg.sender);
        require(proposerStake >= PROPOSAL_THRESHOLD, "insufficient staked balance to propose");

        uint256 proposalId = proposals.length;
        proposals.push(Proposal({
            proposer: msg.sender,
            description: description,
            startBlock: block.number,
            endTime: block.timestamp + votingPeriod,
            forVotes: 0,
            againstVotes: 0,
            abstainVotes: 0
        }));

        emit ProposalCreated(proposalId, msg.sender, description, block.number, block.timestamp + votingPeriod);
        return proposalId;
    }

    // ---------------------------------------------------------------------
    // Voting
    // ---------------------------------------------------------------------

    function vote(uint256 proposalId, VoteType support) external {
        require(proposalId < proposals.length, "invalid proposal id");
        Proposal storage p = proposals[proposalId];

        require(block.timestamp <= p.endTime, "voting closed");
        require(!hasVoted[proposalId][msg.sender], "already voted");

        // Weight is the voter's staked balance AT the proposal's creation
        // block - not their current balance - so stake/unstake after
        // proposal creation cannot change their vote weight on it.
        uint256 weight = stakingPool.balanceAtBlock(msg.sender, p.startBlock);
        require(weight > 0, "no staked balance at proposal creation");

        hasVoted[proposalId][msg.sender] = true;

        if (support == VoteType.For) {
            p.forVotes += weight;
        } else if (support == VoteType.Against) {
            p.againstVotes += weight;
        } else {
            p.abstainVotes += weight;
        }

        emit VoteCast(proposalId, msg.sender, support, weight);
    }

    // ---------------------------------------------------------------------
    // Results
    // ---------------------------------------------------------------------

    function state(uint256 proposalId) public view returns (ProposalState) {
        require(proposalId < proposals.length, "invalid proposal id");
        Proposal storage p = proposals[proposalId];

        if (block.timestamp <= p.endTime) {
            return ProposalState.Active;
        }

        uint256 totalVotes = p.forVotes + p.againstVotes + p.abstainVotes;
        uint256 totalStakedAtStart = stakingPool.totalStakedAtBlock(p.startBlock);
        uint256 quorumRequired = (totalStakedAtStart * QUORUM_BPS) / 10_000;

        if (totalVotes < quorumRequired) {
            return ProposalState.QuorumNotReached;
        }

        return p.forVotes > p.againstVotes ? ProposalState.Succeeded : ProposalState.Defeated;
    }

    function proposalCount() external view returns (uint256) {
        return proposals.length;
    }

    function getProposal(uint256 proposalId) external view returns (
        address proposer,
        string memory description,
        uint256 startBlock,
        uint256 endTime,
        uint256 forVotes,
        uint256 againstVotes,
        uint256 abstainVotes,
        ProposalState currentState
    ) {
        require(proposalId < proposals.length, "invalid proposal id");
        Proposal storage p = proposals[proposalId];
        return (
            p.proposer,
            p.description,
            p.startBlock,
            p.endTime,
            p.forVotes,
            p.againstVotes,
            p.abstainVotes,
            state(proposalId)
        );
    }

    /// @notice Quorum required for a proposal, computed from total staked
    ///         supply at the proposal's creation block.
    function quorumFor(uint256 proposalId) external view returns (uint256) {
        require(proposalId < proposals.length, "invalid proposal id");
        uint256 totalStakedAtStart = stakingPool.totalStakedAtBlock(proposals[proposalId].startBlock);
        return (totalStakedAtStart * QUORUM_BPS) / 10_000;
    }
}

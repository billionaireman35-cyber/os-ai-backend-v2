// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title StakingPool
/// @notice Stake CLOSE to earn linear rewards from a single pre-funded pool.
///         Immutable: no owner, no pause, no upgrade path. Reward funding
///         happens exactly once via fundRewards(). Balances are checkpointed
///         per-block so an external governance contract can read historical
///         voting weight without trusting this contract's current state.
contract StakingPool {
    IERC20 public immutable closeToken;

    // ---- Checkpointing ----
    // A checkpoint records "at this block, this account's staked balance was X".
    // Governance reads these to determine voting power as of a proposal's
    // creation block, so staking/unstaking after a proposal is created cannot
    // change the voting weight already locked in.
    struct Checkpoint {
        uint256 blockNumber;
        uint256 balance;
    }

    mapping(address => Checkpoint[]) private _checkpoints;
    Checkpoint[] private _totalStakedCheckpoints;

    // ---- Reward accounting ----
    uint256 public rewardRatePerSecond; // reward tokens emitted per second, scaled by 1e18
    uint256 public rewardPeriodStart;
    uint256 public rewardPeriodEnd;
    uint256 public rewardsFunded; // becomes true (nonzero) exactly once

    uint256 public rewardPerTokenStored;
    uint256 public lastUpdateTime;
    mapping(address => uint256) public userRewardPerTokenPaid;
    mapping(address => uint256) public rewards;

    uint256 public totalStaked;

    event Staked(address indexed user, uint256 amount);
    event Unstaked(address indexed user, uint256 amount);
    event RewardsClaimed(address indexed user, uint256 amount);
    event RewardsFunded(uint256 amount, uint256 duration, uint256 startTime, uint256 endTime);

    constructor(address _closeToken) {
        require(_closeToken != address(0), "zero token address");
        closeToken = IERC20(_closeToken);
    }

    // ---------------------------------------------------------------------
    // Reward funding - callable exactly once, by anyone, no admin required.
    // Whoever calls this must have already approved this contract to pull
    // `amount` CLOSE. After this call, rewards stream linearly from now
    // until now + duration, then stop forever. There is no top-up path by
    // design: refilling would require an admin key or a second funding
    // round, both of which were explicitly ruled out.
    // ---------------------------------------------------------------------
    function fundRewards(uint256 amount, uint256 duration) external {
        require(rewardsFunded == 0, "rewards already funded");
        require(amount > 0, "amount must be > 0");
        require(duration > 0, "duration must be > 0");

        bool ok = closeToken.transferFrom(msg.sender, address(this), amount);
        require(ok, "transferFrom failed");

        rewardsFunded = amount;
        rewardRatePerSecond = (amount * 1e18) / duration;
        rewardPeriodStart = block.timestamp;
        rewardPeriodEnd = block.timestamp + duration;
        lastUpdateTime = block.timestamp;

        emit RewardsFunded(amount, duration, rewardPeriodStart, rewardPeriodEnd);
    }

    // ---------------------------------------------------------------------
    // Staking
    // ---------------------------------------------------------------------
    function stake(uint256 amount) external {
        require(amount > 0, "amount must be > 0");
        _updateReward(msg.sender);

        bool ok = closeToken.transferFrom(msg.sender, address(this), amount);
        require(ok, "transferFrom failed");

        uint256 newBalance = _currentBalance(msg.sender) + amount;
        _writeCheckpoint(_checkpoints[msg.sender], newBalance);

        totalStaked += amount;
        _writeCheckpoint(_totalStakedCheckpoints, totalStaked);

        emit Staked(msg.sender, amount);
    }

    function unstake(uint256 amount) external {
        require(amount > 0, "amount must be > 0");
        uint256 bal = _currentBalance(msg.sender);
        require(bal >= amount, "insufficient staked balance");

        _updateReward(msg.sender);

        uint256 newBalance = bal - amount;
        _writeCheckpoint(_checkpoints[msg.sender], newBalance);

        totalStaked -= amount;
        _writeCheckpoint(_totalStakedCheckpoints, totalStaked);

        bool ok = closeToken.transfer(msg.sender, amount);
        require(ok, "transfer failed");

        emit Unstaked(msg.sender, amount);
    }

    function claimRewards() external {
        _updateReward(msg.sender);
        uint256 reward = rewards[msg.sender];
        require(reward > 0, "no rewards to claim");
        rewards[msg.sender] = 0;

        bool ok = closeToken.transfer(msg.sender, reward);
        require(ok, "transfer failed");

        emit RewardsClaimed(msg.sender, reward);
    }

    // ---------------------------------------------------------------------
    // Reward math (standard Synthetix-style accumulator)
    // ---------------------------------------------------------------------
    function _lastApplicableTime() internal view returns (uint256) {
        return block.timestamp < rewardPeriodEnd ? block.timestamp : rewardPeriodEnd;
    }

    function rewardPerToken() public view returns (uint256) {
        if (totalStaked == 0) return rewardPerTokenStored;
        uint256 applicable = _lastApplicableTime();
        if (applicable <= lastUpdateTime) return rewardPerTokenStored;
        uint256 elapsed = applicable - lastUpdateTime;
        return rewardPerTokenStored + (elapsed * rewardRatePerSecond) / totalStaked;
    }

    function earned(address account) public view returns (uint256) {
        uint256 bal = _currentBalance(account);
        uint256 rpt = rewardPerToken();
        return (bal * (rpt - userRewardPerTokenPaid[account])) / 1e18 + rewards[account];
    }

    function _updateReward(address account) internal {
        rewardPerTokenStored = rewardPerToken();
        lastUpdateTime = _lastApplicableTime();
        if (account != address(0)) {
            rewards[account] = earned(account);
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
    }

    // ---------------------------------------------------------------------
    // Checkpoint helpers
    // ---------------------------------------------------------------------
    function _currentBalance(address account) internal view returns (uint256) {
        Checkpoint[] storage cps = _checkpoints[account];
        if (cps.length == 0) return 0;
        return cps[cps.length - 1].balance;
    }

    function _writeCheckpoint(Checkpoint[] storage cps, uint256 newBalance) internal {
        if (cps.length > 0 && cps[cps.length - 1].blockNumber == block.number) {
            cps[cps.length - 1].balance = newBalance;
        } else {
            cps.push(Checkpoint({blockNumber: block.number, balance: newBalance}));
        }
    }

    /// @notice Binary search for `account`'s staked balance as of `blockNumber`.
    ///         Used by the governance contract to determine voting weight at
    ///         a proposal's creation block, regardless of later stake changes.
    function balanceAtBlock(address account, uint256 blockNumber) public view returns (uint256) {
        return _checkpointAtBlock(_checkpoints[account], blockNumber);
    }

    /// @notice Same lookup, but for total staked supply - used for quorum math.
    function totalStakedAtBlock(uint256 blockNumber) public view returns (uint256) {
        return _checkpointAtBlock(_totalStakedCheckpoints, blockNumber);
    }

    function _checkpointAtBlock(Checkpoint[] storage cps, uint256 blockNumber) internal view returns (uint256) {
        uint256 len = cps.length;
        if (len == 0) return 0;
        if (cps[0].blockNumber > blockNumber) return 0;
        if (cps[len - 1].blockNumber <= blockNumber) return cps[len - 1].balance;

        uint256 lo = 0;
        uint256 hi = len - 1;
        while (lo < hi) {
            uint256 mid = (lo + hi + 1) / 2;
            if (cps[mid].blockNumber <= blockNumber) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        return cps[lo].balance;
    }

    function currentStakedBalance(address account) external view returns (uint256) {
        return _currentBalance(account);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ICLOSEToken {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title CloseStaking
/// @notice Users lock CLOSE tokens here to unlock message-fee discount tiers.
/// Staked tokens remain fully withdrawable at any time (no lock-up period) -
/// this is a simple balance-gated discount mechanism, not a yield product.
contract CloseStaking {
    ICLOSEToken public immutable closeToken;
    address public owner;
    bool private locked;

    struct StakeInfo {
        uint256 amount;
        uint256 since;
    }

    mapping(address => StakeInfo) public stakes;
    uint256 public totalStaked;

    uint256 public tier1Threshold = 1_000 * 10**18;
    uint256 public tier2Threshold = 10_000 * 10**18;
    uint256 public tier3Threshold = 100_000 * 10**18;

    event Staked(address indexed user, uint256 amount, uint256 newTotal);
    event Unstaked(address indexed user, uint256 amount, uint256 newTotal);
    event ThresholdsUpdated(uint256 t1, uint256 t2, uint256 t3);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier noReentrant() {
        require(!locked, "Reentrant call");
        locked = true;
        _;
        locked = false;
    }

    constructor(address _closeToken) {
        require(_closeToken != address(0), "Zero token address");
        closeToken = ICLOSEToken(_closeToken);
        owner = msg.sender;
    }

    function stake(uint256 amount) external noReentrant {
        require(amount > 0, "Amount must be > 0");
        bool ok = closeToken.transferFrom(msg.sender, address(this), amount);
        require(ok, "Transfer failed");
        stakes[msg.sender].amount += amount;
        stakes[msg.sender].since = block.timestamp;
        totalStaked += amount;
        emit Staked(msg.sender, amount, stakes[msg.sender].amount);
    }

    function unstake(uint256 amount) external noReentrant {
        StakeInfo storage s = stakes[msg.sender];
        require(s.amount >= amount, "Insufficient staked balance");
        s.amount -= amount;
        totalStaked -= amount;
        bool ok = closeToken.transfer(msg.sender, amount);
        require(ok, "Transfer failed");
        emit Unstaked(msg.sender, amount, s.amount);
    }

    function getStakedAmount(address user) external view returns (uint256) {
        return stakes[user].amount;
    }

    function getDiscountTier(address user) external view returns (uint8) {
        uint256 amt = stakes[user].amount;
        if (amt >= tier3Threshold) return 3;
        if (amt >= tier2Threshold) return 2;
        if (amt >= tier1Threshold) return 1;
        return 0;
    }

    function setThresholds(uint256 t1, uint256 t2, uint256 t3) external onlyOwner {
        require(t1 < t2 && t2 < t3, "Thresholds must strictly increase");
        tier1Threshold = t1;
        tier2Threshold = t2;
        tier3Threshold = t3;
        emit ThresholdsUpdated(t1, t2, t3);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}

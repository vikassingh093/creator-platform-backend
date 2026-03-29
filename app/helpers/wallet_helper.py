"""
============================================================
💰 WALLET HELPER
============================================================
Central place for ALL wallet balance operations.
Every credit/debit goes through this file.

USAGE:
  from app.helpers.wallet_helper import credit_wallet, debit_wallet, get_balance

WHY:
  - Prevents negative balance (double-check before deduct)
  - Updates total_added / total_spent correctly
  - Single place to debug balance issues
  - Creator wallet operations too
============================================================
"""

from app.database import execute_query
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# CUSTOMER WALLET
# ─────────────────────────────────────────────────────────

def ensure_wallet_exists(user_id: int) -> dict:
    """
    Get wallet for user. Create if doesn't exist.
    
    Returns:
        Wallet dict: {id, user_id, balance, total_added, total_spent, ...}
    """
    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (user_id,),
        fetch_one=True
    )
    
    if not wallet:
        execute_query(
            "INSERT INTO wallets (user_id, balance, total_added, total_spent) VALUES (%s, 0, 0, 0)",
            (user_id,)
        )
        wallet = execute_query(
            "SELECT * FROM wallets WHERE user_id = %s",
            (user_id,),
            fetch_one=True
        )
        logger.info(f"💰 Created wallet for user {user_id}")
    
    return wallet


def get_balance(user_id: int) -> float:
    """
    Get current wallet balance for user.
    
    Returns: float balance (e.g., 450.50)
    """
    wallet = ensure_wallet_exists(user_id)
    return float(wallet["balance"])


def credit_wallet(user_id: int, amount: float, update_total_added: bool = True) -> dict:
    """
    Add money to customer wallet.
    
    Used for:
      - Razorpay payment success (update_total_added=True)
      - Refund (update_total_added=False — it's not "new" money)
    
    Args:
        user_id: Customer ID
        amount: Amount to add
        update_total_added: If True, increases total_added (for deposits only)
    
    Returns:
        Updated wallet dict
    """
    if amount <= 0:
        raise ValueError(f"Credit amount must be positive, got {amount}")
    
    ensure_wallet_exists(user_id)
    
    if update_total_added:
        execute_query(
            "UPDATE wallets SET balance = balance + %s, total_added = total_added + %s WHERE user_id = %s",
            (amount, amount, user_id)
        )
    else:
        # Refund — don't increase total_added
        execute_query(
            "UPDATE wallets SET balance = balance + %s WHERE user_id = %s",
            (amount, user_id)
        )
    
    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (user_id,),
        fetch_one=True
    )
    
    logger.info(f"💰 Credited ₹{amount} to user {user_id} | New balance: ₹{wallet['balance']}")
    return wallet


def debit_wallet(user_id: int, amount: float) -> dict:
    """
    Deduct money from customer wallet.
    
    SAFETY: Checks balance before deducting. Returns None if insufficient.
    Also updates total_spent.
    
    Args:
        user_id: Customer ID
        amount: Amount to deduct
    
    Returns:
        Updated wallet dict, or None if insufficient balance
    """
    if amount <= 0:
        raise ValueError(f"Debit amount must be positive, got {amount}")
    
    wallet = ensure_wallet_exists(user_id)
    current_balance = float(wallet["balance"])
    
    if current_balance < amount:
        logger.warning(
            f"⚠️ Insufficient balance: user {user_id} has ₹{current_balance}, "
            f"tried to deduct ₹{amount}"
        )
        return None
    
    execute_query(
        "UPDATE wallets SET balance = balance - %s, total_spent = total_spent + %s WHERE user_id = %s",
        (amount, amount, user_id)
    )
    
    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (user_id,),
        fetch_one=True
    )
    
    logger.info(f"💸 Debited ₹{amount} from user {user_id} | New balance: ₹{wallet['balance']}")
    return wallet


def has_sufficient_balance(user_id: int, amount: float) -> bool:
    """
    Check if user has enough balance without deducting.
    
    Used before starting a call/chat to verify affordability.
    """
    balance = get_balance(user_id)
    return balance >= amount


# ─────────────────────────────────────────────────────────
# CREATOR WALLET
# ─────────────────────────────────────────────────────────

def ensure_creator_wallet_exists(creator_id: int) -> dict:
    """
    Get creator wallet. Create if doesn't exist.
    """
    wallet = execute_query(
        "SELECT * FROM creator_wallet WHERE creator_id = %s",
        (creator_id,),
        fetch_one=True
    )
    
    if not wallet:
        execute_query(
            "INSERT INTO creator_wallet (creator_id, balance, total_earned, total_withdrawn) VALUES (%s, 0, 0, 0)",
            (creator_id,)
        )
        wallet = execute_query(
            "SELECT * FROM creator_wallet WHERE creator_id = %s",
            (creator_id,),
            fetch_one=True
        )
        logger.info(f"💰 Created creator wallet for creator {creator_id}")
    
    return wallet


def get_creator_balance(creator_id: int) -> float:
    """
    Get current creator wallet balance.
    """
    wallet = ensure_creator_wallet_exists(creator_id)
    return float(wallet["balance"])


def credit_creator_wallet(creator_id: int, amount: float) -> dict:
    """
    Add earnings to creator wallet.
    
    Called after a call/chat/content purchase ends.
    Amount should be AFTER commission deduction (creator's share only).
    
    Args:
        creator_id: Creator user ID
        amount: Creator's earnings (after commission)
    
    Returns:
        Updated creator wallet dict
    """
    if amount <= 0:
        logger.warning(f"⚠️ Tried to credit ₹{amount} to creator {creator_id} — skipping")
        return ensure_creator_wallet_exists(creator_id)
    
    ensure_creator_wallet_exists(creator_id)
    
    execute_query(
        "UPDATE creator_wallet SET balance = balance + %s, total_earned = total_earned + %s WHERE creator_id = %s",
        (amount, amount, creator_id)
    )
    
    wallet = execute_query(
        "SELECT * FROM creator_wallet WHERE creator_id = %s",
        (creator_id,),
        fetch_one=True
    )
    
    logger.info(f"💰 Creator {creator_id} earned ₹{amount} | Balance: ₹{wallet['balance']}")
    return wallet


def debit_creator_wallet(creator_id: int, amount: float) -> dict:
    """
    Deduct from creator wallet (for withdrawals).
    
    Returns None if insufficient balance.
    """
    if amount <= 0:
        raise ValueError(f"Debit amount must be positive, got {amount}")
    
    wallet = ensure_creator_wallet_exists(creator_id)
    current_balance = float(wallet["balance"])
    
    if current_balance < amount:
        logger.warning(
            f"⚠️ Insufficient creator balance: creator {creator_id} has ₹{current_balance}, "
            f"tried to withdraw ₹{amount}"
        )
        return None
    
    execute_query(
        "UPDATE creator_wallet SET balance = balance - %s, total_withdrawn = total_withdrawn + %s WHERE creator_id = %s",
        (amount, amount, creator_id)
    )
    
    wallet = execute_query(
        "SELECT * FROM creator_wallet WHERE creator_id = %s",
        (creator_id,),
        fetch_one=True
    )
    
    logger.info(f"💸 Creator {creator_id} withdrew ₹{amount} | Balance: ₹{wallet['balance']}")
    return wallet
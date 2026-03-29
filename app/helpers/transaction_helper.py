"""
============================================================
📊 TRANSACTION HELPER
============================================================
Central place for ALL transaction recording.
Every money movement in the app goes through this file.

USAGE:
  from app.helpers.transaction_helper import record_transaction, record_refund

WHY:
  - Single place to debug all transactions
  - Ensures creator_amount, commission_amount always recorded
  - Easy to add new transaction types
  - All reports pull from same data format
============================================================
"""

from app.database import execute_query
import logging

logger = logging.getLogger(__name__)


def get_platform_commission_percent() -> float:
    """
    Get platform commission % from settings table.
    Default: 30% (platform takes 30%, creator gets 70%)
    
    Returns: float (e.g., 30.0 means 30%)
    """
    try:
        row = execute_query(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_commission_percent'",
            fetch_one=True
        )
        if row:
            return float(row["setting_value"])
    except Exception as e:
        logger.error(f"❌ Failed to get commission setting: {e}")
    return 30.0  # safe default


def calculate_split(total_amount: float, commission_percent: float = None) -> dict:
    """
    Calculate how money is split between creator and platform.
    
    Example: total=100, commission=30%
      → creator gets ₹70, platform gets ₹30
    
    Args:
        total_amount: Total amount charged to customer
        commission_percent: Override commission %. If None, reads from DB.
    
    Returns:
        {
            "total": 100.00,
            "creator_amount": 70.00,
            "commission_amount": 30.00,
            "commission_percent": 30.0
        }
    """
    if commission_percent is None:
        commission_percent = get_platform_commission_percent()
    
    commission_amount = round(total_amount * (commission_percent / 100), 2)
    creator_amount = round(total_amount - commission_amount, 2)
    
    return {
        "total": round(total_amount, 2),
        "creator_amount": creator_amount,
        "commission_amount": commission_amount,
        "commission_percent": commission_percent
    }


def record_transaction(
    user_id: int,
    txn_type: str,
    amount: float,
    description: str,
    reference_id: str = None,
    status: str = "success",
    creator_id: int = None,
    creator_amount: float = 0.0,
    commission_amount: float = 0.0,
    razorpay_order_id: str = None
) -> int:
    """
    Record a transaction in the transactions table.
    
    This is the ONLY function that should INSERT into transactions.
    All routers (wallet, calls, chat, content, admin) must use this.
    
    Args:
        user_id: Customer/user who is charged or credited
        txn_type: One of: add_money, audio_call, video_call, chat, 
                  content, payout, refund, withdrawal, adjustment, commission
        amount: Amount in INR
        description: Human-readable description
        reference_id: Link to call_room id, payment_id, etc.
        status: pending / success / failed
        creator_id: Creator involved (for earnings tracking)
        creator_amount: How much creator earns from this transaction
        commission_amount: How much platform earns
        razorpay_order_id: Razorpay order ID (for payment transactions)
    
    Returns:
        Transaction ID (int)
    """
    try:
        execute_query(
            """
            INSERT INTO transactions 
                (user_id, type, amount, description, reference_id, 
                 razorpay_order_id, status, creator_id, creator_amount, commission_amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id, txn_type, round(amount, 2), description,
                reference_id, razorpay_order_id, status,
                creator_id, round(creator_amount, 2), round(commission_amount, 2)
            )
        )
        
        # Get the inserted transaction ID
        result = execute_query("SELECT LAST_INSERT_ID() as id", fetch_one=True)
        txn_id = result["id"] if result else None
        
        logger.info(
            f"📊 Transaction recorded: #{txn_id} | {txn_type} | "
            f"₹{amount} | user={user_id} | creator={creator_id} | "
            f"creator_amt=₹{creator_amount} | commission=₹{commission_amount}"
        )
        
        return txn_id
        
    except Exception as e:
        logger.error(f"❌ Failed to record transaction: {e}")
        raise


def record_call_transaction(
    user_id: int,
    creator_id: int,
    call_type: str,
    duration_seconds: int,
    total_cost: float,
    room_id: int
) -> int:
    """
    Record a call transaction with proper creator/commission split.
    
    Called from calls.py when a call ends.
    
    Args:
        user_id: Customer who was charged
        creator_id: Creator who earned
        call_type: "audio" or "video"
        duration_seconds: Call duration in seconds
        total_cost: Total amount charged to customer
        room_id: call_rooms.id for reference
    
    Returns:
        Transaction ID
    """
    # Calculate split
    split = calculate_split(total_cost)
    
    # Format duration for description
    minutes = duration_seconds // 60
    seconds = duration_seconds % 60
    duration_str = f"{duration_seconds}s"
    if minutes > 0:
        duration_str = f"{minutes}m {seconds}s"
    
    txn_type = "video_call" if call_type == "video" else "audio_call"
    description = f"{call_type.capitalize()} call - {duration_str}"
    
    return record_transaction(
        user_id=user_id,
        txn_type=txn_type,
        amount=total_cost,
        description=description,
        reference_id=f"call_{room_id}",
        creator_id=creator_id,
        creator_amount=split["creator_amount"],
        commission_amount=split["commission_amount"]
    )


def record_chat_transaction(
    user_id: int,
    creator_id: int,
    duration_seconds: int,
    total_cost: float,
    room_id: int
) -> int:
    """
    Record a chat transaction with proper creator/commission split.
    
    Called from chat.py when a paid chat session ends.
    """
    split = calculate_split(total_cost)
    
    minutes = duration_seconds // 60
    seconds = duration_seconds % 60
    duration_str = f"{duration_seconds}s"
    if minutes > 0:
        duration_str = f"{minutes}m {seconds}s"
    
    return record_transaction(
        user_id=user_id,
        txn_type="chat",
        amount=total_cost,
        description=f"Chat - {duration_str}",
        reference_id=f"chat_{room_id}",
        creator_id=creator_id,
        creator_amount=split["creator_amount"],
        commission_amount=split["commission_amount"]
    )


def record_content_transaction(
    user_id: int,
    creator_id: int,
    content_id: int,
    amount: float,
    content_title: str = "Content"
) -> int:
    """
    Record a content purchase transaction.
    """
    split = calculate_split(amount)
    
    return record_transaction(
        user_id=user_id,
        txn_type="content",
        amount=amount,
        description=f"Purchased: {content_title}",
        reference_id=f"content_{content_id}",
        creator_id=creator_id,
        creator_amount=split["creator_amount"],
        commission_amount=split["commission_amount"]
    )


def record_add_money(
    user_id: int,
    amount: float,
    razorpay_payment_id: str = None,
    razorpay_order_id: str = None
) -> int:
    """
    Record wallet recharge transaction.
    """
    return record_transaction(
        user_id=user_id,
        txn_type="add_money",
        amount=amount,
        description=f"Wallet recharge ₹{amount}",
        reference_id=razorpay_payment_id,
        razorpay_order_id=razorpay_order_id,
        status="success"
    )


def record_refund(
    user_id: int,
    amount: float,
    reason: str,
    reference_id: str = None,
    creator_id: int = None
) -> int:
    """
    Record a refund transaction.
    
    Called automatically (rejected call with tick) or manually (admin refund).
    
    Args:
        user_id: Customer getting money back
        amount: Refund amount
        reason: Why the refund happened
        reference_id: Original transaction reference (e.g., "call_201")
        creator_id: Creator involved (if applicable)
    """
    return record_transaction(
        user_id=user_id,
        txn_type="refund",
        amount=amount,
        description=f"Refund: {reason}",
        reference_id=f"refund_{reference_id}" if reference_id else None,
        creator_id=creator_id,
        status="success"
    )


def record_withdrawal(
    creator_id: int,
    amount: float,
    withdrawal_request_id: int
) -> int:
    """
    Record creator withdrawal transaction.
    
    Called when admin approves a withdrawal request.
    """
    return record_transaction(
        user_id=creator_id,
        txn_type="withdrawal",
        amount=amount,
        description=f"Withdrawal approved - ₹{amount}",
        reference_id=f"withdrawal_{withdrawal_request_id}",
        creator_id=creator_id,
        status="success"
    )
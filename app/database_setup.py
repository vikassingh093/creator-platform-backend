from app.database import get_db, execute_query
import logging

logger = logging.getLogger(__name__)

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        phone VARCHAR(15) NOT NULL UNIQUE,
        email VARCHAR(100) UNIQUE,
        profile_photo VARCHAR(255),
        user_type ENUM('user', 'creator', 'admin') DEFAULT 'user',
        is_active BOOLEAN DEFAULT TRUE,
        is_blocked BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS creator_profiles (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        specialty VARCHAR(100),
        bio TEXT,
        call_rate DECIMAL(10,2) DEFAULT 0,
        chat_rate DECIMAL(10,2) DEFAULT 0,
        is_online BOOLEAN DEFAULT FALSE,
        is_approved BOOLEAN DEFAULT FALSE,
        is_rejected BOOLEAN DEFAULT FALSE,
        total_earnings DECIMAL(10,2) DEFAULT 0,
        rating DECIMAL(3,2) DEFAULT 0,
        total_reviews INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallets (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        balance DECIMAL(10,2) DEFAULT 0,
        total_added DECIMAL(10,2) DEFAULT 0,
        total_spent DECIMAL(10,2) DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        type ENUM('add_money', 'call', 'chat', 'content', 'payout') NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        description VARCHAR(255),
        reference_id VARCHAR(100),
        status ENUM('pending', 'success', 'failed') DEFAULT 'success',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content (
        id INT AUTO_INCREMENT PRIMARY KEY,
        creator_id INT NOT NULL,
        title VARCHAR(255) NOT NULL,
        type ENUM('photo', 'photo_pack', 'video') NOT NULL,
        price DECIMAL(10,2) DEFAULT 0,
        is_free BOOLEAN DEFAULT FALSE,
        duration VARCHAR(20),
        thumbnail VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (creator_id) REFERENCES creator_profiles(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_files (
        id INT AUTO_INCREMENT PRIMARY KEY,
        content_id INT NOT NULL,
        file_url VARCHAR(255) NOT NULL,
        file_order INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (content_id) REFERENCES content(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_purchases (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        content_id INT NOT NULL,
        amount_paid DECIMAL(10,2) NOT NULL,
        purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_purchase (user_id, content_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (content_id) REFERENCES content(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_rooms (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        creator_id INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_room (user_id, creator_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (creator_id) REFERENCES creator_profiles(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        room_id INT NOT NULL,
        sender_id INT NOT NULL,
        message TEXT NOT NULL,
        is_read BOOLEAN DEFAULT FALSE,
        read_at TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (room_id) REFERENCES chat_rooms(id) ON DELETE CASCADE,
        FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        merchant_transaction_id VARCHAR(100) UNIQUE NOT NULL,
        phonepe_transaction_id VARCHAR(100),
        amount DECIMAL(10,2) NOT NULL,
        status ENUM('pending', 'success', 'failed', 'refunded') DEFAULT 'pending',
        payment_method VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payout_requests (
        id INT AUTO_INCREMENT PRIMARY KEY,
        creator_id INT NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        upi_id VARCHAR(100) NOT NULL,
        status ENUM('pending', 'paid', 'rejected') DEFAULT 'pending',
        admin_note VARCHAR(255),
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP NULL,
        FOREIGN KEY (creator_id) REFERENCES creator_profiles(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        creator_id INT NOT NULL,
        rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_review (user_id, creator_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (creator_id) REFERENCES creator_profiles(id) ON DELETE CASCADE
    )
    """,
]

def create_tables():
    with get_db() as conn:
        with conn.cursor() as cursor:
            for table_sql in TABLES:
                cursor.execute(table_sql)
    logger.info("✅ All tables created successfully!")

def create_admin():
    existing = execute_query(
        "SELECT id FROM users WHERE phone = %s",
        ("0000000000",),
        fetch_one=True
    )
    if not existing:
        execute_query(
            "INSERT INTO users (name, phone, email, user_type) VALUES (%s, %s, %s, %s)",
            ("Admin", "0000000000", "admin@creatorhub.com", "admin")
        )
        admin = execute_query(
            "SELECT id FROM users WHERE phone = %s",
            ("0000000000",),
            fetch_one=True
        )
        execute_query(
            "INSERT INTO wallets (user_id, balance) VALUES (%s, %s)",
            (admin["id"], 0)
        )
        logger.info("✅ Default admin created! Phone: 0000000000")
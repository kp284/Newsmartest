# database.py
"""
Handles all database operations for the bot using aiosqlite.

This module provides an asynchronous interface for creating tables,
adding/updating users, managing promotions, groups, and all other
persistent data. Each function opens and closes its own connection
to ensure thread safety in an async environment.
"""

import aiosqlite
import logging
from datetime import datetime, timedelta

DB_NAME = 'promotion_bot.db'
logger = logging.getLogger(__name__)

def get_db():
    """Returns a connection context manager to the database."""
    return aiosqlite.connect(DB_NAME)

async def initialize_database():
    """
    Creates all necessary tables if they don't exist and performs schema migrations.
    This should be called once when the bot starts.
    """
    async with get_db() as db:
        # --- Table Creations ---
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                credits INTEGER DEFAULT 5,
                referral_credits INTEGER DEFAULT 0,
                inviter_id INTEGER,
                is_premium BOOLEAN DEFAULT FALSE,
                premium_expiry DATE,
                is_banned BOOLEAN DEFAULT FALSE,
                daily_promo_runs INTEGER DEFAULT 2,
                image_broadcasts_left INTEGER DEFAULT 100,
                normal_promo_text TEXT,
                normal_promo_url TEXT,
                normal_promo_chat_id INTEGER,
                normal_promo_message_id INTEGER,
                force_join_channel_id INTEGER,
                clicks_received INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                added_by_user_id INTEGER,
                is_admin BOOLEAN DEFAULT FALSE,
                UNIQUE(group_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promotions (
                promo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                promoter_user_id INTEGER,
                promo_type TEXT,
                channel_id INTEGER,
                promo_text TEXT,
                promo_url TEXT,
                budget INTEGER DEFAULT 0,
                promo_chat_id INTEGER,
                promo_message_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS claimed_promos (
                user_id INTEGER,
                promo_id INTEGER,
                PRIMARY KEY (user_id, promo_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS feature_flags (
                name TEXT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT TRUE
            )
        ''')
        
        flags = ['group_promotion', 'force_join_promotion', 'premium_image_caption']
        for flag in flags:
            await db.execute('INSERT OR IGNORE INTO feature_flags (name) VALUES (?)', (flag,))

        # --- Schema Migrations ---
        logger.info("Checking for necessary schema migrations...")
        
        # Migration for users table
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'normal_promo_chat_id' not in columns:
            logger.info("Adding 'normal_promo_chat_id' to 'users' table.")
            await db.execute('ALTER TABLE users ADD COLUMN normal_promo_chat_id INTEGER')
        if 'normal_promo_message_id' not in columns:
            logger.info("Adding 'normal_promo_message_id' to 'users' table.")
            await db.execute('ALTER TABLE users ADD COLUMN normal_promo_message_id INTEGER')

        # Migration for promotions table
        cursor = await db.execute("PRAGMA table_info(promotions)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'promo_chat_id' not in columns:
            logger.info("Adding 'promo_chat_id' to 'promotions' table.")
            await db.execute('ALTER TABLE promotions ADD COLUMN promo_chat_id INTEGER')
        if 'promo_message_id' not in columns:
            logger.info("Adding 'promo_message_id' to 'promotions' table.")
            await db.execute('ALTER TABLE promotions ADD COLUMN promo_message_id INTEGER')

        await db.commit()
        logger.info("Database initialization and migration check complete.")

# --- User Management ---

async def add_user(user_id, username, inviter_id=None):
    async with get_db() as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id, username, inviter_id) VALUES (?, ?, ?)', (user_id, username, inviter_id))
        await db.commit()

async def get_user(user_id):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def get_all_user_ids():
    async with get_db() as db:
        cursor = await db.execute('SELECT user_id FROM users WHERE is_banned = FALSE')
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def update_user_credits(user_id, amount):
    async with get_db() as db:
        await db.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (amount, user_id))
        await db.commit()

async def update_referral_credits(user_id, amount):
    async with get_db() as db:
        await db.execute('UPDATE users SET referral_credits = referral_credits + ? WHERE user_id = ?', (amount, user_id))
        await db.commit()

async def ban_user(user_id, is_banned: bool):
    async with get_db() as db:
        await db.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (is_banned, user_id))
        await db.commit()

async def set_premium(user_id, days):
    expiry_date = datetime.now() + timedelta(days=days)
    async with get_db() as db:
        await db.execute('''
            UPDATE users SET is_premium = TRUE, premium_expiry = ?, daily_promo_runs = 5, image_broadcasts_left = 100
            WHERE user_id = ?
        ''', (expiry_date.date(), user_id))
        await db.commit()

async def remove_premium(user_id):
    async with get_db() as db:
        await db.execute('''
            UPDATE users SET is_premium = FALSE, premium_expiry = NULL, daily_promo_runs = 2
            WHERE user_id = ?
        ''', (user_id,))
        await db.commit()

async def use_promo_run(user_id):
    async with get_db() as db:
        await db.execute('UPDATE users SET daily_promo_runs = daily_promo_runs - 1 WHERE user_id = ? AND daily_promo_runs > 0', (user_id,))
        await db.commit()

async def use_image_broadcast_run(user_id, count):
    async with get_db() as db:
        await db.execute('UPDATE users SET image_broadcasts_left = image_broadcasts_left - ? WHERE user_id = ?', (count, user_id))
        await db.commit()
        
async def get_random_users_for_broadcast(exclude_user_id, limit):
    async with get_db() as db:
        cursor = await db.execute('SELECT user_id FROM users WHERE user_id != ? AND is_banned = FALSE ORDER BY RANDOM() LIMIT ?', (exclude_user_id, limit))
        return [row[0] for row in await cursor.fetchall()]

# --- Promotion Management ---

async def set_normal_promo(user_id, text, url, chat_id, message_id):
    async with get_db() as db:
        await db.execute('UPDATE users SET normal_promo_text = ?, normal_promo_url = ?, normal_promo_chat_id = ?, normal_promo_message_id = ? WHERE user_id = ?', (text, url, chat_id, message_id, user_id))
        await db.commit()

async def set_force_join_channel(user_id, channel_id):
    async with get_db() as db:
        await db.execute('UPDATE users SET force_join_channel_id = ? WHERE user_id = ?', (channel_id, user_id))
        await db.commit()

async def add_promotion(user_id, promo_type, budget, channel_id=None, text=None, url=None, chat_id=None, message_id=None):
    async with get_db() as db:
        await db.execute(
            'INSERT INTO promotions (promoter_user_id, promo_type, budget, channel_id, promo_text, promo_url, promo_chat_id, promo_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, promo_type, budget, channel_id, text, url, chat_id, message_id)
        )
        await db.commit()

async def get_random_promotion(user_id):
    async with get_db() as db:
        query = '''
            SELECT p.promo_id, p.promoter_user_id, p.promo_type, p.channel_id, p.promo_text, p.promo_url, p.promo_chat_id, p.promo_message_id
            FROM promotions p
            LEFT JOIN claimed_promos cp ON p.promo_id = cp.promo_id AND cp.user_id = ?
            WHERE p.promoter_user_id != ? AND cp.promo_id IS NULL AND p.budget > 0
            ORDER BY RANDOM() LIMIT 1
        '''
        cursor = await db.execute(query, (user_id, user_id))
        return await cursor.fetchone()

async def claim_promo(user_id, promo_id):
    async with get_db() as db:
        await db.execute('INSERT OR IGNORE INTO claimed_promos (user_id, promo_id) VALUES (?, ?)', (user_id, promo_id))
        await db.commit()

async def decrement_promo_budget(promo_id):
    async with get_db() as db:
        await db.execute('UPDATE promotions SET budget = budget - 1 WHERE promo_id = ? AND budget > 0', (promo_id,))
        await db.commit()

async def has_claimed_promo(user_id, promo_id):
    async with get_db() as db:
        cursor = await db.execute('SELECT 1 FROM claimed_promos WHERE user_id = ? AND promo_id = ?', (user_id, promo_id))
        return await cursor.fetchone() is not None

async def increment_clicks_received(user_id):
    async with get_db() as db:
        await db.execute('UPDATE users SET clicks_received = clicks_received + 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_leaderboard():
    async with get_db() as db:
        cursor = await db.execute('SELECT username, clicks_received FROM users WHERE clicks_received > 0 ORDER BY clicks_received DESC LIMIT 10')
        return await cursor.fetchall()
        
# --- Group Management ---

async def add_group(group_id, added_by_user_id, is_admin):
    async with get_db() as db:
        await db.execute('INSERT INTO groups (group_id, added_by_user_id, is_admin) VALUES (?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET is_admin = excluded.is_admin',
                         (group_id, added_by_user_id, is_admin))
        await db.commit()

async def get_group(group_id):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM groups WHERE group_id = ?', (group_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def get_random_groups(limit):
    async with get_db() as db:
        cursor = await db.execute('SELECT group_id FROM groups WHERE is_admin = TRUE ORDER BY RANDOM() LIMIT ?', (limit,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

# --- Feature Flags ---
async def get_feature_flag(name):
    async with get_db() as db:
        cursor = await db.execute('SELECT is_enabled FROM feature_flags WHERE name = ?', (name,))
        row = await cursor.fetchone()
        return row[0] if row else False

async def set_feature_flag(name, is_enabled: bool):
    async with get_db() as db:
        await db.execute('UPDATE feature_flags SET is_enabled = ? WHERE name = ?', (is_enabled, name))
        await db.commit()

async def get_all_feature_flags():
    async with get_db() as db:
        cursor = await db.execute('SELECT name, is_enabled FROM feature_flags')
        return await cursor.fetchall()

# --- Scheduled Job Queries ---
async def execute_daily_reset():
    async with get_db() as db:
        await db.execute('UPDATE users SET credits = credits + referral_credits')
        await db.execute('UPDATE users SET daily_promo_runs = 2 WHERE is_premium = FALSE')
        await db.execute('UPDATE users SET daily_promo_runs = 5 WHERE is_premium = TRUE')
        await db.commit()

async def execute_weekly_reset():
    async with get_db() as db:
        await db.execute('UPDATE users SET clicks_received = 0')
        await db.commit()

async def reset_all_premium_image_broadcasts():
    async with get_db() as db:
        await db.execute('UPDATE users SET image_broadcasts_left = 100 WHERE is_premium = TRUE')
        await db.commit()

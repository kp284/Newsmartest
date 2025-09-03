# keyboards.py
"""
Defines all inline keyboard layouts used by the bot.

This module centralizes the creation of InlineKeyboardMarkup objects,
making it easy to manage and update the bot's user interface.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config

async def main_menu_keyboard(user_id) -> InlineKeyboardMarkup:
    """
    Returns the main menu keyboard.
    If the user is an admin, it integrates admin controls directly into the menu.
    """
    keyboard = [
        [InlineKeyboardButton("🚀 Promote My Link", callback_data='promote_link'),
         InlineKeyboardButton("📢 Group Share", callback_data='start_group_share')],
        [InlineKeyboardButton("🎁 Earn Credits", callback_data='earn_credits'),
         InlineKeyboardButton("👤 My Account", callback_data='my_account')],
        [InlineKeyboardButton("👥 Referral Link", callback_data='referral_link'),
         InlineKeyboardButton("📊 Leaderboard", callback_data='leaderboard')],
        [InlineKeyboardButton("💎 Premium Upgrade", callback_data='premium_upgrade'),
         InlineKeyboardButton("📸 Broadcast Img Caption", callback_data='premium_broadcast')],
        [InlineKeyboardButton("➕ Add Me to Group", callback_data='add_to_group')]
    ]
    
    if user_id in config.ADMIN_IDS:
        admin_rows = [
            [InlineKeyboardButton("——— 👑 Admin Menu 👑 ———", callback_data='admin_menu_title')],
            [InlineKeyboardButton("💬 Broadcast", callback_data='admin_broadcast'), InlineKeyboardButton("📊 User Stats", callback_data='admin_stats')],
            [InlineKeyboardButton("➕ Add Premium", callback_data='admin_add_premium'), InlineKeyboardButton("🗑️ Remove Premium", callback_data='admin_remove_premium')],
            [InlineKeyboardButton("🚫 Ban User", callback_data='admin_ban_user'), InlineKeyboardButton("✅ Unban User", callback_data='admin_unban_user')],
            [InlineKeyboardButton("⚙️ Feature Flags", callback_data='admin_feature_flags')]
        ]
        keyboard.extend(admin_rows)

    return InlineKeyboardMarkup(keyboard)

def promotion_management_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the main promotion menu."""
    keyboard = [
        [InlineKeyboardButton("📢 Create a Promotion", callback_data='create_promotion')],
        [InlineKeyboardButton("✏️ Set/Update Normal Link", callback_data='set_normal_link')],
        [InlineKeyboardButton("🔔 Set/Update Force-Join Channel", callback_data='set_force_channel')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main')]
    ]
    return InlineKeyboardMarkup(keyboard)
    
async def feature_flags_keyboard(flags: list) -> InlineKeyboardMarkup:
    """Dynamically creates a keyboard for toggling feature flags."""
    keyboard = []
    for name, enabled in flags:
        status_icon = "✅" if enabled else "❌"
        display_name = name.replace('_', ' ').title()
        button = InlineKeyboardButton(f"{display_name}: {status_icon}", callback_data=f"toggle_flag_{name}")
        keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)


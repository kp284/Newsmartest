# handlers.py
"""
Contains all the callback functions for the bot's commands, buttons, and messages.

This module is responsible for the core logic of the bot's interactions,
including user registration, handling promotions, referrals, tasks, and
admin functionalities.
"""
import logging
import asyncio
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError

import config
import database as db
from keyboards import main_menu_keyboard, promotion_management_keyboard, feature_flags_keyboard

logger = logging.getLogger(__name__)

# User Conversation states
LINK_TEXT, LINK_URL = range(2)
CHANNEL_ID = range(1)
AWAIT_PROMO_TYPE_FOR_CREATION, AWAIT_BUDGET = range(2, 4)
AWAIT_IMAGE_FOR_BROADCAST, AWAIT_BROADCAST_COUNT = range(4, 6)

# Admin Conversation States
BROADCAST_MESSAGE = range(10, 11)
AWAIT_USER_ID_FOR_PREMIUM, AWAIT_PREMIUM_DAYS = range(11, 13)
AWAIT_USER_ID_FOR_REMOVE_PREMIUM = range(13, 14)
AWAIT_USER_ID_FOR_BAN = range(14, 15)
AWAIT_USER_ID_FOR_UNBAN = range(15, 16)
AWAIT_USER_ID_FOR_STATS = range(16, 17)


async def check_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if a user exists in the DB, is banned, and adds them if new.
    Returns True if the user is okay to proceed, False otherwise.
    """
    user = update.effective_user
    db_user = await db.get_user(user.id)

    if db_user and db_user['is_banned']:
        if update.message: 
            await update.message.reply_text("You are banned from using this bot.")
        elif update.callback_query:
            await update.callback_query.answer("You are banned from using this bot.", show_alert=True)
        return False

    if not db_user:
        inviter_id = None
        if context.args and update.effective_chat.type == ChatType.PRIVATE:
            try:
                inviter_id = int(context.args[0])
                if inviter_id != user.id:
                    await db.update_referral_credits(inviter_id, 2)
                    await context.bot.send_message(chat_id=inviter_id, text=f"🎉 New user @{user.username} joined via your link! You get +2 permanent daily credits.")
            except (ValueError, IndexError, TelegramError): pass
        await db.add_user(user.id, user.username, inviter_id)
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the welcome message and main menu."""
    if update.message and not await check_user(update, context): return
    user, user_id = update.effective_user, update.effective_user.id
    db_user = await db.get_user(user_id)
    if not db_user: await db.add_user(user_id, user.username); db_user = await db.get_user(user_id)
    credits, referral_credits = db_user['credits'], db_user['referral_credits']
    welcome_text = (f"👋 **Welcome, {user.first_name}!**\n\nPromote your content or earn credits by completing tasks.\n\n"
                    f"💰 **Balance:** `{credits}` Credits\n📈 **Daily Referral Bonus:** `{referral_credits}` Credits")
    keyboard = await main_menu_keyboard(user_id)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        except TelegramError as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing message in start: {e}")
    else:
        await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all inline button presses that are not part of a conversation."""
    query, user_id = update.callback_query, update.effective_user.id
    await query.answer()
    if not await check_user(update, context):
        return

    data = query.data
    actions = {
        'promote_link': lambda u, c: u.callback_query.edit_message_text("**🚀 Promotion Menu**\n\nSet up your content or create a new promotion.", reply_markup=promotion_management_keyboard(), parse_mode=ParseMode.MARKDOWN),
        'start_group_share': start_group_share_flow,
        'execute_group_share_final': execute_group_share,
        'earn_credits': tasks,
        'referral_link': referral,
        'leaderboard': leaderboard,
        'premium_upgrade': premium_info,
        'add_to_group': add_to_group,
        'my_account': my_account,
        'back_to_main': start,
        'admin_feature_flags': admin_feature_flags,
        'admin_back': start,
    }
    if data in actions: await actions[data](update, context)
    elif data.startswith('toggle_flag_'):
        if user_id not in config.ADMIN_IDS: return
        feature_name = data.replace('toggle_flag_', '')
        current_status = await db.get_feature_flag(feature_name)
        await db.set_feature_flag(feature_name, not current_status)
        await admin_feature_flags(update, context, is_edit=True)
    elif data.startswith('claim_'): await handle_claim_promo(update, context, data)
    elif data.startswith('verify_'): await handle_verify_promo(update, context, data)
    elif data.startswith('report_'): await handle_report_start(update, context, data)

async def handle_claim_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query, user_id = update.callback_query, update.effective_user.id
    _, promo_id_str, promoter_id_str = data.split('_')
    promo_id, promoter_id = int(promo_id_str), int(promoter_id_str)
    if await db.has_claimed_promo(user_id, promo_id):
        await query.answer("You have already completed this task.", show_alert=True); return
    await db.claim_promo(user_id, promo_id)
    await db.decrement_promo_budget(promo_id)
    db_user = await db.get_user(user_id)
    reward = 2 if db_user and db_user['is_premium'] else 1
    await db.update_user_credits(user_id, reward)
    await db.increment_clicks_received(promoter_id)
    await query.edit_message_text(f"✅ Success! You've earned {reward} credit(s).")
    try: await context.bot.send_message(promoter_id, f"🎉 Someone completed your normal promotion! You received +1 view.")
    except TelegramError as e: logger.warning(f"Could not notify promoter {promoter_id}: {e}")

async def handle_verify_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query, user_id = update.callback_query, update.effective_user.id
    _, promo_id_str, channel_id_str, promoter_id_str = data.split('_')
    promo_id, channel_id, promoter_id = int(promo_id_str), int(channel_id_str), int(promoter_id_str)
    if await db.has_claimed_promo(user_id, promo_id):
        await query.answer("You have already completed this task.", show_alert=True); return
    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            await db.claim_promo(user_id, promo_id)
            await db.decrement_promo_budget(promo_id)
            db_user = await db.get_user(user_id)
            reward = 4 if db_user and db_user['is_premium'] else 2
            await db.update_user_credits(user_id, reward)
            await db.increment_clicks_received(promoter_id)
            await query.edit_message_text(f"✅ Verified! You've earned {reward} credits.")
            try: await context.bot.send_message(promoter_id, "🎉 Someone joined your channel from a promotion! You received +1 view.")
            except TelegramError as e: logger.warning(f"Could not notify promoter {promoter_id}: {e}")
        else: await query.answer("You haven't joined the channel yet.", show_alert=True)
    except TelegramError as e: await query.edit_message_text(f"❌ Error: Could not verify membership. Error: {e}")

async def handle_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    _, promoter_id = data.split('_')
    context.user_data['promoter_to_report'] = promoter_id
    await query.edit_message_text("Please forward the message you want to report. It must be a message originally sent by me.")

# --- Main Feature Handlers ---
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, bot = update.effective_user.id, await context.bot.get_me()
    referral_link = f"https://t.me/{bot.username}?start={user_id}"
    text = f"👥 **Your Referral Link**\n\nShare this for **+2 permanent daily credits** per new user!\n\n`{referral_link}`"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    board = await db.get_leaderboard()
    text = "🏆 **Weekly Leaderboard (Top 10)**\n_Based on total views received._\n\n"
    if not board: text += "The leaderboard is empty."
    else:
        for i, (username, clicks) in enumerate(board):
            rank_icon = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
            text += f"{rank_icon} @{username or 'Anonymous'} - `{clicks}` views\n"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")]])
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    promo = await db.get_random_promotion(user_id)
    if not promo:
        text, keyboard = "No new tasks available. Check back later!", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]])
        if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=keyboard); return
        else: await update.message.reply_text(text, reply_markup=keyboard); return

    promo_id, promoter_id, promo_type, channel_id, promo_text, promo_url, promo_chat_id, promo_message_id = promo
    
    keyboard_buttons = [
        [InlineKeyboardButton("➡️ Next Task", callback_data="earn_credits"), InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")],
        [InlineKeyboardButton("⚠️ Report", callback_data=f"report_{promoter_id}")]
    ]

    if update.callback_query:
        await update.callback_query.message.delete()

    if promo_type == 'normal' and promo_chat_id and promo_message_id:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=promo_chat_id,
            message_id=promo_message_id
        )
        keyboard_buttons.insert(0, [InlineKeyboardButton("✅ Claim Credits", callback_data=f"claim_{promo_id}_{promoter_id}")])
        keyboard_buttons.insert(0, [InlineKeyboardButton("🔗 Visit Link", url=promo_url)])
        await context.bot.send_message(user_id, "Complete the task above to earn credits.", reply_markup=InlineKeyboardMarkup(keyboard_buttons))

    elif promo_type == 'normal':
        text = f"**Task: Visit Link**\n\n{promo_text}"
        keyboard_buttons.insert(0, [InlineKeyboardButton("✅ Claim Credits", callback_data=f"claim_{promo_id}_{promoter_id}")])
        keyboard_buttons.insert(0, [InlineKeyboardButton("🔗 Visit Link", url=promo_url)])
        await context.bot.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(keyboard_buttons), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    else: # force_join
        try:
            chat = await context.bot.get_chat(channel_id)
            invite_link = chat.invite_link or await context.bot.export_chat_invite_link(chat_id=channel_id)
            text = f"**Task: Join Channel**\n\nJoin **{chat.title}** to earn credits."
            keyboard_buttons.insert(0, [InlineKeyboardButton("✅ Verify & Claim", callback_data=f"verify_{promo_id}_{channel_id}_{promoter_id}")])
            keyboard_buttons.insert(0, [InlineKeyboardButton(f"➡️ Join {chat.title}", url=invite_link)])
            await context.bot.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(keyboard_buttons), parse_mode=ParseMode.MARKDOWN)
        except TelegramError as e:
            logger.error(f"Error fetching channel for task: {e}")
            await context.bot.send_message(user_id, "Error with this task.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Next", callback_data="earn_credits")]]))

async def premium_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "💎 **Premium Membership**\n\n- ✨ Double rewards & higher daily credits\n- ✨ More group promotions\n- ✨ Broadcast images with captions!\n\nContact admin for payment."
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{config.OWNER_USERNAME}")], [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]])
    await update.callback_query.edit_message_text(text, reply_markup=keyboard)

async def add_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = await context.bot.get_me()
    add_link = f"https://t.me/{bot.username}?startgroup={update.effective_user.id}"
    text = "➕ **Add Me to Your Group**\n\nAdd me to your group & make me admin for a credit bonus!\n\n`+5` (Normal) / `+10` (Premium)"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add to Group", url=add_link)], [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]])
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user_id = update.callback_query, update.effective_user.id
    user_data = await db.get_user(user_id)
    if not user_data: await query.edit_message_text("Could not find your account details."); return
    premium_status = "Yes ✅" if user_data['is_premium'] else "No ❌"
    if user_data['is_premium'] and user_data['premium_expiry']: premium_status += f" (Expires: {user_data['premium_expiry']})"
    normal_promo = f"`{user_data['normal_promo_text']}`\nURL: `{user_data['normal_promo_url']}`" if user_data['normal_promo_text'] else "`Not set`"
    if user_data.get('normal_promo_chat_id'):
        normal_promo += "\n_(Formatting is preserved for broadcast)_"
    force_join = f"`{user_data['force_join_channel_id']}`" if user_data['force_join_channel_id'] else "`Not set`"
    text = (f"👤 **My Account**\n\n**ID:** `{user_id}` | **Username:** @{user_data['username']}\n"
            f"**Credits:** `{user_data['credits']}`\n**Daily Referral Bonus:** `{user_data['referral_credits']}`\n**Premium:** {premium_status}\n\n"
            f"**Usage:**\n - Group Promos Left: `{user_data['daily_promo_runs']}`\n - Image Broadcasts Left: `{user_data['image_broadcasts_left']}`\n\n"
            f"**Saved Promotions:**\n - **Normal Link:**\n{normal_promo}\n - **Force-Join Channel:** {force_join}")
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]]), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# --- Conversation Handlers ---
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled."); context.user_data.clear(); await start(update, context); return ConversationHandler.END

async def promote_normal_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send text for your promotion.\n\n/cancel to abort."); return LINK_TEXT
async def get_link_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['promo_text'] = update.message.text
    context.user_data['promo_chat_id'] = update.message.chat_id
    context.user_data['promo_message_id'] = update.message.message_id
    await update.message.reply_text("✅ Text saved. Now, send the URL.\n\n/cancel.")
    return LINK_URL

async def get_link_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    text = context.user_data.get('promo_text')
    chat_id = context.user_data.get('promo_chat_id')
    message_id = context.user_data.get('promo_message_id')
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("Invalid URL. Send a valid one.")
        return LINK_URL
    await db.set_normal_promo(update.effective_user.id, text, url, chat_id, message_id)
    
    await update.message.reply_text("✅ **Normal promotion saved!** The message below is what will be broadcasted:")
    await context.bot.copy_message(
        chat_id=update.effective_chat.id,
        from_chat_id=chat_id,
        message_id=message_id
    )
    await update.message.reply_text(f"The URL for the button will be: {url}", disable_web_page_preview=True)

    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def set_force_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send Channel ID or @username. Bot must be admin.\n\n/cancel."); return CHANNEL_ID
async def get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, channel_input = update.effective_user.id, update.message.text
    await update.message.reply_text(f"Checking `{channel_input}`...", parse_mode=ParseMode.MARKDOWN)
    try:
        chat, bot_member = await context.bot.get_chat(channel_input), await context.bot.get_chat_member(channel_input, context.bot.id)
        if bot_member.status != 'administrator': await update.message.reply_text("❌ **Error:** I'm not an admin there."); return CHANNEL_ID
        await db.set_force_join_channel(user_id, chat.id)
        await update.message.reply_text(f"✅ **Force-join channel set to {chat.title}!**", parse_mode=ParseMode.MARKDOWN)
        await start(update, context); return ConversationHandler.END
    except TelegramError as e: await update.message.reply_text(f"❌ **Error:** Could not access channel. {e}"); return CHANNEL_ID

async def create_promotion_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, user = update.callback_query, await db.get_user(update.effective_user.id)
    buttons = []
    if user.get('normal_promo_url'): buttons.append([InlineKeyboardButton("🔗 Normal Link Promo", callback_data="create_promo_normal")])
    if user.get('force_join_channel_id'): buttons.append([InlineKeyboardButton("📣 Force-Join Promo", callback_data="create_promo_force_join")])
    if not buttons: await query.answer("Set up a promotion link/channel first!", show_alert=True); return ConversationHandler.END
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data='promote_link')])
    await query.edit_message_text("Which of your saved promotions would you like to create a task for?", reply_markup=InlineKeyboardMarkup(buttons))
    return AWAIT_PROMO_TYPE_FOR_CREATION
async def get_promotion_type_for_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, promo_type = update.callback_query, update.callback_query.data.replace('create_promo_', '')
    context.user_data['promo_type_to_create'] = promo_type
    user = await db.get_user(update.effective_user.id)
    await query.message.reply_text(f"How many credits to spend? (1 credit = 1 user).\n\nBalance: `{user['credits']}`\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
    return AWAIT_BUDGET
async def get_promotion_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, message = update.effective_user.id, update.message
    try: budget = int(message.text)
    except ValueError: await message.reply_text("Please send a valid number."); return AWAIT_BUDGET
    user = await db.get_user(user_id)
    if not (0 < budget <= user['credits']): await message.reply_text(f"Invalid amount. Min: 1, Max: {user['credits']}."); return AWAIT_BUDGET
    promo_type = context.user_data['promo_type_to_create']
    if promo_type == 'normal':
        await db.add_promotion(
            user_id, 'normal', budget,
            text=user['normal_promo_text'], url=user['normal_promo_url'],
            chat_id=user.get('normal_promo_chat_id'), message_id=user.get('normal_promo_message_id')
        )
    else: # force_join
        await db.add_promotion(user_id, 'force_join', budget, channel_id=user['force_join_channel_id'])
    await db.update_user_credits(user_id, -budget)
    await message.reply_text(f"✅ **Promotion created!** `{budget}` credits spent.", parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear(); await start(update, context); return ConversationHandler.END

async def premium_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, user = update.callback_query, await db.get_user(update.effective_user.id)
    if not user['is_premium']: await query.answer("This is a premium-only feature.", show_alert=True); return ConversationHandler.END
    await query.message.reply_text("Send the image with caption to broadcast.\n\n/cancel"); return AWAIT_IMAGE_FOR_BROADCAST
async def get_image_for_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    context.user_data['broadcast_chat_id'] = message.chat_id
    context.user_data['broadcast_message_id'] = message.message_id
    user = await db.get_user(update.effective_user.id)
    await message.reply_text(f"Image received. How many users to send to?\n\n- Max: `{user['image_broadcasts_left']}`\n- Cost: 1 credit per 10 users.\n- Balance: `{user['credits']}`\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
    return AWAIT_BROADCAST_COUNT

async def get_broadcast_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user_id = update.message, update.effective_user.id
    try:
        count = int(message.text)
    except ValueError:
        await message.reply_text("Send a valid number.")
        return AWAIT_BROADCAST_COUNT
    user = await db.get_user(user_id)
    cost = math.ceil(count / 10)
    if count <= 0:
        await message.reply_text("Must be positive.")
        return AWAIT_BROADCAST_COUNT
    if count > user['image_broadcasts_left']:
        await message.reply_text(f"You can only broadcast to `{user['image_broadcasts_left']}` more users today.", parse_mode=ParseMode.MARKDOWN)
        return AWAIT_BROADCAST_COUNT
    if cost > user['credits']:
        await message.reply_text(f"Insufficient funds. This costs `{cost}` credits but you have `{user['credits']}`.", parse_mode=ParseMode.MARKDOWN)
        return AWAIT_BROADCAST_COUNT
    
    await message.reply_text("Starting broadcast...")
    target_users, s, f = await db.get_random_users_for_broadcast(user_id, count), 0, 0
    chat_id = context.user_data['broadcast_chat_id']
    message_id = context.user_data['broadcast_message_id']
    
    for target_id in target_users:
        try:
            await context.bot.copy_message(target_id, chat_id, message_id)
            s += 1
            await asyncio.sleep(0.2)
        except TelegramError as e:
            f += 1
            logger.warning(f"Premium broadcast fail for {target_id}: {e}")
            
    await db.use_image_broadcast_run(user_id, s)
    await db.update_user_credits(user_id, -cost)
    await message.reply_text(f"✅ Broadcast complete!\n- Sent to: `{s}`\n- Failed: `{f}`\n- Cost: `{cost}` credits", parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def new_group_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot, group = await context.bot.get_me(), update.message.chat
    if bot.id not in [m.id for m in update.message.new_chat_members]: return

    if update.message.from_user.is_bot:
        logger.info(f"Bot was added to group '{group.title}' by another bot ({update.message.from_user.username}). Ignoring.")
        return

    logger.info(f"Bot added to group '{group.title}' ({group.id}) by user {update.message.from_user.id}")
    adder_user_id = update.message.from_user.id
    
    try:
        existing_group = await db.get_group(group.id)
        is_admin = (await context.bot.get_chat_member(group.id, bot.id)).status == 'administrator'

        if existing_group:
            logger.info(f"Bot re-added to group '{group.title}' ({group.id}). No credits will be awarded.")
            if is_admin and not existing_group['is_admin']:
                await db.add_group(group.id, adder_user_id, is_admin)
                await context.bot.send_message(group.id, "Thanks for promoting me to admin! I'm ready to receive promotions.")
            elif not is_admin:
                await context.bot.send_message(group.id, "For me to work, please promote me to admin.")
            return

        await db.add_group(group.id, adder_user_id, is_admin)

        if is_admin:
            member_count = await context.bot.get_chat_member_count(group.id)
            if member_count > 600:
                user = await db.get_user(adder_user_id)
                reward = 10 if user and user['is_premium'] else 5
                await db.update_user_credits(adder_user_id, reward)
                await context.bot.send_message(adder_user_id, f"🎉 Thanks for making me admin in '{group.title}'! You got `{reward}` credits as the group has over 600 members.", parse_mode=ParseMode.MARKDOWN)
                await context.bot.send_message(group.id, "Hello! I'm ready to receive promotions.")
            else:
                await context.bot.send_message(adder_user_id, f"Thanks for making me admin in '{group.title}'. To receive credits, the group must have over 600 members. Current count: {member_count}.")
                await context.bot.send_message(group.id, "Hello! I'm ready to receive promotions.")
        else:
            await context.bot.send_message(group.id, "Hello! For me to work, please promote me to admin.")

    except TelegramError as e:
        logger.error(f"Error processing new group {group.id}: {e}")

async def start_group_share_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the group share flow, showing a confirmation message."""
    query = update.callback_query
    user = await db.get_user(update.effective_user.id)
    if not user: return

    # Check for prerequisites and reply accordingly
    if user['daily_promo_runs'] <= 0:
        text = "You have no group promotion runs left for today."
        if query: await query.answer(text, show_alert=True)
        else: await update.message.reply_text(text)
        return

    if not user['normal_promo_text'] or not user['normal_promo_url']:
        text = "You need to set up your 'Normal Link' promotion first.\n /start"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")]])
        if query:
            await query.answer(text, show_alert=True)
            await query.edit_message_text(text,reply_markup=keyboard)
        else: 
            await update.message.reply_text(text,reply_markup=keyboard)
        return

    # All checks passed, create the confirmation message
    limit = 10 if user['is_premium'] else 5
    text = (f"**Confirm Group Promotion**\n\n"
            f"You are about to send the following promotion to `{limit}` random groups:\n\n"
            f"**Message:**\n---\n_{user['normal_promo_text']}_\n---\n\n"
            f"This will use 1 of your `{user['daily_promo_runs']}` remaining daily runs.\n\n"
            f"Are you sure you want to proceed?")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Send it!", callback_data="execute_group_share_final")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")]])
    
    # Send the confirmation message
    if query:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def execute_group_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Executes the group sharing after user confirmation.
    Works for both button (callback) and direct command.
    """
    query = getattr(update, "callback_query", None)
    message = getattr(update, "message", None)
    user_id = update.effective_user.id

    user = await db.get_user(user_id)
    if not user or user['daily_promo_runs'] <= 0 or not user['normal_promo_url']:
        text = "Something went wrong. Please try again."
        if query:
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]])
            )
        elif message:
            await message.reply_text(text)
        return

    limit = 10 if user['is_premium'] else 5
    groups = await db.get_random_groups(limit)
    if not groups:
        if query:
            await query.answer("No available groups for promotion right now. Please try again later.", show_alert=True)
            await start(update, context)
        elif message:
            await message.reply_text("No available groups for promotion right now. Please try again later.")
        return

    # Inform user that promotion is starting
    if query:
        await query.edit_message_text(
            f"🚀 **Sending...**\nPlease wait while your promotion is sent to {len(groups)} groups.",
            parse_mode=ParseMode.MARKDOWN
        )
    elif message:
        await message.reply_text(
            f"🚀 **Sending...**\nPlease wait while your promotion is sent to {len(groups)} groups.",
            parse_mode=ParseMode.MARKDOWN
        )

    # Send to groups
    s_count, f_count = 0, 0
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Visit Link", url=user['normal_promo_url'])]])
    promo_chat_id = user.get('normal_promo_chat_id')
    promo_message_id = user.get('normal_promo_message_id')

    for group_id in groups:
        try:
            if promo_chat_id and promo_message_id:
                sent_message = await context.bot.copy_message(
                    chat_id=group_id,
                    from_chat_id=promo_chat_id,
                    message_id=promo_message_id,
                )
                await context.bot.edit_message_reply_markup(
                    chat_id=group_id,
                    message_id=sent_message.message_id,
                    reply_markup=keyboard,
                )
            else:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=user['normal_promo_text'],
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            s_count += 1
            await asyncio.sleep(0.5)
        except TelegramError as e:
            f_count += 1
            logger.warning(f"Failed to send group share to group {group_id}: {e}")

    # Update DB usage
    await db.use_promo_run(user_id)
    updated_user = await db.get_user(user_id)
    report_text = (
        f"✅ **Promotion Sent!**\n\n"
        f"- Successfully sent to: `{s_count}` groups\n"
        f"- Failed to send to: `{f_count}` groups\n\n"
        f"You have `{updated_user['daily_promo_runs']}` group promotion runs left today."
    )

    if query:
        await query.edit_message_text(
            report_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif message:
        await message.reply_text(report_text, parse_mode=ParseMode.MARKDOWN)

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send message to broadcast.\n\n/cancel"); return BROADCAST_MESSAGE
async def get_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user_ids = update.message, await db.get_all_user_ids()
    await message.reply_text(f"Broadcasting to {len(user_ids)} users...")
    s, f, b = 0, 0, 0
    for user_id in user_ids:
        try:
            await context.bot.copy_message(user_id, message.chat_id, message.message_id)
            s += 1
            await asyncio.sleep(0.1)
        except TelegramError as e:
            f += 1
            logger.warning(f"Broadcast failed for {user_id}: {e}")
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                b += 1   # Count as blocked/deactivated, but don’t ban
                # removed: await db.ban_user(user_id, True)

    report = (
        f"**🚀 Broadcast Complete**\n\n"
        f"✅ Sent: `{s}`\n"
        f"❌ Failed: `{f}`\n"
        f"🚫 Blocked/Deactivated: `{b}`"
    )
    await message.reply_text(report, parse_mode=ParseMode.MARKDOWN)
    await start(update, context)
    return ConversationHandler.END


async def admin_add_premium_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send User ID to grant Premium.\n\n/cancel."); return AWAIT_USER_ID_FOR_PREMIUM
async def get_user_id_for_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['target_user_id'] = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAIT_USER_ID_FOR_PREMIUM
    await update.message.reply_text("Now, send the number of days for premium (e.g., 30)."); return AWAIT_PREMIUM_DAYS
async def get_premium_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: days = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid number."); return AWAIT_PREMIUM_DAYS
    user_id = context.user_data['target_user_id']
    await db.set_premium(user_id, days)
    await update.message.reply_text(f"✅ User `{user_id}` is now premium for {days} days.", parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear(); await start(update, context); return ConversationHandler.END

async def admin_remove_premium_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send User ID to remove Premium.\n\n/cancel."); return AWAIT_USER_ID_FOR_REMOVE_PREMIUM
async def get_user_id_for_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: user_id = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAIT_USER_ID_FOR_REMOVE_PREMIUM
    await db.remove_premium(user_id); await update.message.reply_text(f"✅ Premium removed from user `{user_id}`.", parse_mode=ParseMode.MARKDOWN); await start(update, context); return ConversationHandler.END

async def admin_ban_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send User ID to BAN.\n\n/cancel."); return AWAIT_USER_ID_FOR_BAN
async def get_user_id_for_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: user_id = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAIT_USER_ID_FOR_BAN
    await db.ban_user(user_id, True); await update.message.reply_text(f"🚫 User `{user_id}` has been banned.", parse_mode=ParseMode.MARKDOWN); await start(update, context); return ConversationHandler.END

async def admin_unban_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send User ID to UNBAN.\n\n/cancel."); return AWAIT_USER_ID_FOR_UNBAN
async def get_user_id_for_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: user_id = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAIT_USER_ID_FOR_UNBAN
    await db.ban_user(user_id, False); await update.message.reply_text(f"✅ User `{user_id}` has been unbanned.", parse_mode=ParseMode.MARKDOWN); await start(update, context); return ConversationHandler.END

async def admin_get_stats_start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.message.reply_text("Send User ID for stats.\n\n/cancel."); return AWAIT_USER_ID_FOR_STATS
async def get_user_id_for_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: user_id = int(update.message.text)
    except ValueError: await update.message.reply_text("Invalid ID."); return AWAIT_USER_ID_FOR_STATS
    user_data = await db.get_user(user_id)
    text = f"No data for user `{user_id}`." if not user_data else f"📊 **Stats for User:** `{user_id}`\n\n" + "\n".join([f" - **{k.replace('_', ' ').title()}:** `{v}`" for k,v in user_data.items()])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN); await start(update, context); return ConversationHandler.END

async def admin_feature_flags(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False):
    flags, keyboard = await db.get_all_feature_flags(), await feature_flags_keyboard(await db.get_all_feature_flags())
    text = "⚙️ **Feature Control Panel**\n\nEnable or disable features for all users."
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else: await update.message.reply_text(text, reply_markup=keyboard)

async def group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wrapper to make the group share flow available as a command."""
    await start_group_share_flow(update, context)

async def handle_report_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get('promoter_to_report'): return
    promoter_id, reporter = context.user_data['promoter_to_report'], update.effective_user
    report_message = f"⚠️ **New Report**\n\n**Reporter:** @{reporter.username} (`{reporter.id}`)\n**Reported User ID:** `{promoter_id}`"
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, report_message, parse_mode=ParseMode.MARKDOWN)
            await context.bot.forward_message(admin_id, update.message.chat_id, update.message.message_id)
        except TelegramError as e: logger.error(f"Failed to send report to admin {admin_id}: {e}")
    await update.message.reply_text("✅ Report sent to administrators.")
    context.user_data.clear(); await start(update, context)

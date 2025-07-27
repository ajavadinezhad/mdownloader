import os
import asyncio
import logging
import tempfile
import shutil
from urllib.parse import urlparse
import re
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import requests

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', './downloads')
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024  # Convert MB to bytes

# Channel/Group settings for forced subscription
REQUIRED_CHANNELS = os.getenv('REQUIRED_CHANNELS', '').split(',') if os.getenv('REQUIRED_CHANNELS') else []
# Example: REQUIRED_CHANNELS=@your_channel,@your_group,-1001234567890

# Validate bot token
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables!")
    logger.error("💡 Create a .env file with your bot token:")
    logger.error("   TELEGRAM_BOT_TOKEN=your_bot_token_here")
    exit(1)

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class MediaDownloaderBot:
    def __init__(self):
        self.supported_platforms = {
            'youtube.com': 'YouTube',
            'youtu.be': 'YouTube',
            'twitter.com': 'Twitter/X',
            'x.com': 'Twitter/X',
            'instagram.com': 'Instagram',
            'soundcloud.com': 'SoundCloud',
            'tiktok.com': 'TikTok',
            'facebook.com': 'Facebook',
            'vimeo.com': 'Vimeo'
        }
        # Store URLs temporarily with short IDs
        self.url_cache = {}
    
    def is_supported_url(self, url):
        """Check if the URL is from a supported platform"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove 'www.' prefix if present
            if domain.startswith('www.'):
                domain = domain[4:]
            
            # Log for debugging
            logger.info(f"Checking URL: {url}")
            logger.info(f"Parsed domain: {domain}")
            
            is_supported = any(platform in domain for platform in self.supported_platforms.keys())
            logger.info(f"Is supported: {is_supported}")
            
            return is_supported
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {e}")
            return False
    
    def store_url(self, url, user_id):
        """Store URL with a short ID for callback data"""
        import hashlib
        import time
        
        # Create a short unique ID
        url_hash = hashlib.md5(f"{url}{user_id}{time.time()}".encode()).hexdigest()[:8]
        self.url_cache[url_hash] = url
        
        # Clean old entries (keep only last 100)
        if len(self.url_cache) > 100:
            old_keys = list(self.url_cache.keys())[:-50]
            for key in old_keys:
                del self.url_cache[key]
        
        return url_hash
    
    def get_url(self, url_id):
        """Retrieve URL from short ID"""
        return self.url_cache.get(url_id)
    
    def get_platform_name(self, url):
        """Get the platform name from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            if domain.startswith('www.'):
                domain = domain[4:]
            
            for platform, name in self.supported_platforms.items():
                if platform in domain:
                    return name
            return "Unknown"
        except:
            return "Unknown"
    
    async def download_media(self, url, format_type='best'):
        """Download media using yt-dlp with enhanced support for all platforms"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Base yt-dlp options
            base_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'format': format_type,
                'noplaylist': True,
                'extractaudio': format_type == 'audio',
                'audioformat': 'mp3' if format_type == 'audio' else None,
                'audioquality': '192' if format_type == 'audio' else None,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'abort_on_unavailable_fragment': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'ignoreerrors': False,
            }
            
            # Platform-specific strategies
            strategies = []
            
            if 'twitter.com' in url or 'x.com' in url:
                # Strategy 1: Modern Twitter approach with legacy API
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    'extractor_args': {
                        'twitter': {
                            'api': 'legacy',
                        }
                    }
                })
                strategies.append(strategy1)
                
                # Strategy 2: Syndication API approach
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best[height<=480]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
                        'Accept': '*/*',
                        'Accept-Language': 'en-US,en;q=0.5',
                    },
                    'extractor_args': {
                        'twitter': {
                            'api': 'syndication',
                        }
                    }
                })
                strategies.append(strategy2)
                
                # Strategy 3: No API specification (let yt-dlp choose)
                strategy3 = base_opts.copy()
                strategy3.update({
                    'format': 'best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                    },
                    'retries': 2,
                })
                strategies.append(strategy3)
                
                # Strategy 4: Try converting x.com to twitter.com
                strategy4 = base_opts.copy()
                strategy4.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Referer': 'https://twitter.com/',
                    },
                    'convert_url': True  # Flag to convert URL
                })
                strategies.append(strategy4)
                
            elif 'youtube.com' in url or 'youtu.be' in url:
                # YouTube strategies
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best' if format_type == 'video' else 'bestaudio[ext=m4a]/bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-us,en;q=0.5',
                        'Accept-Encoding': 'gzip,deflate',
                        'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                        'Keep-Alive': '300',
                        'Connection': 'keep-alive',
                    },
                    'extractor_args': {
                        'youtube': {
                            'skip': ['hls'],
                            'player_skip': ['webpage'],
                            'player_client': ['android', 'web'],
                        }
                    },
                    'age_limit': 21,
                    'cookies': os.getenv('YTDLP_COOKIES')  # ← Directly here, no validation
                })
                strategies.append(strategy1)
                
                # YouTube Strategy 2: Android client only
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'worst[height>=360]/worst' if format_type == 'video' else 'worst',
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android'],
                            'skip': ['dash', 'hls'],
                        }
                    },
                    'cookies': os.getenv('YTDLP_COOKIES')  # ← Directly here, no validation
                })
                strategies.append(strategy2)
                
            elif 'instagram.com' in url:
                # Instagram strategies
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=1080]',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Cache-Control': 'max-age=0',
                    },
                    'sleep_interval': 2,
                    'max_sleep_interval': 8,
                    'extractor_args': {
                        'instagram': {
                            'include_onion_urls': True,
                            'api_version': 'v1',
                        }
                    }
                })
                strategies.append(strategy1)
                
                # Instagram Strategy 2: Android app simulation
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best',
                    'http_headers': {
                        'User-Agent': 'Instagram 302.0.0.23.113 Android (33/13; 420dpi; 1080x2340; samsung; SM-G991B; o1s; exynos2100; en_US; 314665256)',
                        'X-IG-App-ID': '124024574287414',
                    },
                    'sleep_interval': 3,
                })
                strategies.append(strategy2)
                
            else:
                # Default strategy for other platforms
                strategy = base_opts.copy()
                strategy.update({
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                })
                
                # Platform-specific format adjustments
                if 'soundcloud.com' in url:
                    strategy['format'] = 'bestaudio/best'
                    if format_type == 'audio':
                        strategy['postprocessors'] = [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }]
                elif 'tiktok.com' in url:
                    strategy['format'] = 'best[height<=720]'
                
                strategies.append(strategy)
            
            # Try each strategy
            last_error = None
            for i, strategy in enumerate(strategies):
                try:
                    logger.info(f"Trying download strategy {i+1}/{len(strategies)}")
                    
                    # Handle URL conversion for Twitter
                    current_url = url
                    if strategy.get('convert_url') and ('x.com' in url):
                        current_url = url.replace('x.com', 'twitter.com')
                        del strategy['convert_url']  # Remove custom flag
                    
                    with yt_dlp.YoutubeDL(strategy) as ydl:
                        # Extract info first
                        try:
                            info = ydl.extract_info(current_url, download=False)
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.warning(f"Strategy {i+1} failed during info extraction: {e}")
                            
                            # Handle specific errors
                            if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Platform detected automated access. Try a different video or wait a few minutes."
                            elif any(keyword in error_msg for keyword in ['private', 'unavailable', 'not found']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ This content is private, unavailable, or was removed."
                            elif any(keyword in error_msg for keyword in ['age', 'restricted']):
                                return None, "❌ This content is age-restricted and cannot be downloaded."
                            elif any(keyword in error_msg for keyword in ['login required', 'rate-limit', 'rate limit']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Login required or rate-limited. This content may be private or restricted."
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ Error accessing content: {str(e)[:100]}..."
                        
                        title = info.get('title', 'Unknown')
                        duration = info.get('duration', 0)
                        
                        # Check file size
                        filesize = info.get('filesize') or info.get('filesize_approx', 0)
                        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                        if filesize and filesize > MAX_FILE_SIZE:
                            return None, f"❌ File too large ({filesize // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                        
                        # Download the media
                        try:
                            ydl.download([current_url])
                            
                            # Find downloaded file
                            files = os.listdir(temp_dir)
                            if files:
                                filepath = os.path.join(temp_dir, files[0])
                                logger.info(f"Successfully downloaded with strategy {i+1}")
                                return filepath, None
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Download failed - no file created"
                        
                        except Exception as e:
                            error_msg = str(e).lower()
                            last_error = str(e)
                            logger.warning(f"Strategy {i+1} failed during download: {e}")
                            
                            if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Platform detected automated access. Try again later."
                            elif 'http error 403' in error_msg:
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Access forbidden. Content may be region-restricted."
                            elif 'http error 404' in error_msg:
                                return None, "❌ Content not found. The link may be broken or removed."
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ Download failed: {str(e)[:100]}..."
                
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Strategy {i+1} failed with exception: {e}")
                    if i < len(strategies) - 1:
                        continue
                    break
            
            # All strategies failed
            if last_error:
                error_msg = str(last_error).lower()
                if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                    return None, "❌ Platform detected automated access. All bypass attempts failed."
                else:
                    return None, f"❌ All download strategies failed. Last error: {str(last_error)[:100]}..."
            else:
                return None, "❌ All download strategies failed for unknown reasons."
        
        except Exception as e:
            logger.error(f"Unexpected error in download_media: {e}")
            return None, f"❌ Unexpected error: {str(e)[:100]}..."
        
        finally:
            # Cleanup happens in the calling function
            pass

# Initialize bot
bot = MediaDownloaderBot()

async def check_user_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list]:
    """Check if user is member of required channels/groups"""
    if not REQUIRED_CHANNELS:
        return True, []  # No channels required
    
    not_joined = []
    
    for channel in REQUIRED_CHANNELS:
        channel = channel.strip()
        if not channel:
            continue
            
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked']:
                not_joined.append(channel)
        except Exception as e:
            logger.warning(f"Could not check membership for {channel}: {e}")
            # If we can't check, assume not joined for security
            not_joined.append(channel)
    
    return len(not_joined) == 0, not_joined

async def create_join_keyboard(not_joined_channels: list) -> InlineKeyboardMarkup:
    """Create keyboard with join buttons for required channels"""
    keyboard = []
    
    for channel in not_joined_channels:
        # Create join button for each channel
        if channel.startswith('@'):
            channel_name = channel[1:]  # Remove @ symbol
            url = f"https://t.me/{channel_name}"
        elif channel.startswith('-100'):
            # For channel IDs, you need to provide the channel username manually
            # or use a mapping in your .env file
            url = f"https://t.me/joinchat/{channel[4:]}"  # This might not work for all cases
        else:
            url = f"https://t.me/{channel}"
        
        keyboard.append([InlineKeyboardButton(f"📢 Join {channel}", url=url)])
    
    # Add check membership button
    keyboard.append([InlineKeyboardButton("✅ Check Membership", callback_data="check_membership")])
    
    return InlineKeyboardMarkup(keyboard)

async def membership_required_message(not_joined_channels: list) -> str:
    """Create membership required message"""
    message = """
🔒 **Membership Required**

To use this bot, you must join our channel(s):

"""
    
    for channel in not_joined_channels:
        message += f"📢 {channel}\n"
    
    message += """
**Steps:**
1. Click the join button(s) below
2. Join the channel(s)
3. Click "✅ Check Membership"
4. Start using the bot!

This helps support our community 🙏
    """
    
    return message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user_id = update.message.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    # User is member, show welcome message
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    welcome_text = f"""
🎬 <b>Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• YouTube (videos & audio)
• Twitter/X (videos)
• Instagram (posts & stories)
• TikTok (videos)
• SoundCloud (audio)
• Facebook (videos)
• Vimeo (videos)

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.
    """
    
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle URL messages"""
    user_id = update.message.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    original_text = update.message.text.strip()
    
    # Remove bot mention if present (for group chats)
    text = original_text
    if update.message.chat.type in ['group', 'supergroup']:
        # Remove @botname mentions
        import re
        text = re.sub(r'@\w+', '', text).strip()
    
    # Extract URL from text
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, text)
    
    if urls:
        url = urls[0]  # Take the first URL found
    else:
        # If no URL found in cleaned text, try original text
        urls = re.findall(url_pattern, original_text)
        if urls:
            url = urls[0]
        else:
            # No URL found at all - ignore this message silently
            # Only respond if it looks like they're trying to send a URL
            if any(word in text.lower() for word in ['http', 'www.', '.com', '.org', '.net', 'youtube', 'instagram', 'soundcloud', 'twitter', 'tiktok']):
                await update.message.reply_text("❌ Please send a valid URL starting with http:// or https://")
            return
    
    logger.info(f"Received URL: {url}")
    
    # Check if platform is supported
    if not bot.is_supported_url(url):
        supported = ", ".join(bot.supported_platforms.values())
        logger.warning(f"Unsupported URL: {url}")
        
        # Get domain for display
        try:
            domain = urlparse(url).netloc
        except:
            domain = "unknown"
        
        await update.message.reply_text(
            f"❌ Platform not supported.\n\n"
            f"Supported platforms: {supported}\n\n"
            f"Your URL domain: {domain}",
            parse_mode=None
        )
        return
    
    platform = bot.get_platform_name(url)
    logger.info(f"Detected platform: {platform}")
    
    # Store URL with short ID for callback data
    url_id = bot.store_url(url, user_id)
    
    # For SoundCloud, only show audio option
    if 'soundcloud.com' in url.lower():
        keyboard = [
            [InlineKeyboardButton("🎵 Download Audio", callback_data=f"audio|{url_id}")]
        ]
    else:
        # Create format selection keyboard for other platforms
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{url_id}")
            ]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 Detected: {platform}\n\nChoose download format:",
        reply_markup=reply_markup
    )

async def handle_membership_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle membership check callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Check if user is now member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if is_member:
        # User has joined all channels
        max_size_mb = MAX_FILE_SIZE // (1024*1024)
        welcome_text = f"""
✅ <b>Welcome! You're now verified!</b>

🎬 <b>Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• YouTube (videos & audio)
• Twitter/X (videos)  
• Instagram (posts & stories)
• TikTok (videos)
• SoundCloud (audio)
• Facebook (videos)
• Vimeo (videos)

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format  
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.
        """
        
        await query.edit_message_text(welcome_text, parse_mode='HTML')
    else:
        # User still hasn't joined all channels
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await query.edit_message_text(
            message + "\n\n❌ <b>You still need to join the channels above!</b>",
            reply_markup=keyboard,
            parse_mode='HTML'
        )

async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle format selection callback"""
    query = update.callback_query
    await query.answer()
    
    # Handle membership check
    if query.data == "check_membership":
        await handle_membership_check(update, context)
        return
    
    user_id = query.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await query.edit_message_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    data_parts = query.data.split('|', 1)
    if len(data_parts) != 2:
        await query.edit_message_text("❌ Invalid selection")
        return
    
    format_type, url_id = data_parts
    
    # Retrieve the actual URL
    url = bot.get_url(url_id)
    if not url:
        await query.edit_message_text("❌ Session expired. Please send the URL again.")
        return
    
    platform = bot.get_platform_name(url)
    
    # Show downloading message
    format_emoji = "🎥" if format_type == "video" else "🎵"
    await query.edit_message_text(
        f"{format_emoji} Downloading {format_type} from {platform}...\n\n"
        f"⏳ This may take a moment depending on file size."
    )
    
    # Download the media
    try:
        filepath, error = await bot.download_media(url, format_type)
        
        if error:
            error_text = f"❌ {error}"
            
            # Add helpful suggestions for common errors
            if "YouTube detected automated access" in error:
                error_text += "\n\nSuggestions:\n"
                error_text += "• Try a different YouTube video\n"
                error_text += "• Wait 10-15 minutes before trying again\n"
                error_text += "• Use shorter videos (under 5 minutes)\n"
                error_text += "• Try youtube.com links instead of youtu.be"
            elif "private" in error.lower() or "unavailable" in error.lower():
                error_text += "\n\nTry: Make sure the video is public and available in your region"
            elif "age-restricted" in error.lower():
                error_text += "\n\nNote: Age-restricted content cannot be downloaded"
            
            await query.edit_message_text(error_text)
            return
        
        if not filepath or not os.path.exists(filepath):
            await query.edit_message_text("❌ Download failed - file not found")
            return
        
        # Get file info
        file_size = os.path.getsize(filepath)
        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB.")
            return
        
        # Send the file
        await query.edit_message_text(f"📤 Uploading {format_type}...")
        
        with open(filepath, 'rb') as file:
            if format_type == 'audio':
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=file,
                    caption=f"🎵 Downloaded from {platform}"
                )
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=file,
                    caption=f"🎥 Downloaded from {platform}",
                    supports_streaming=True
                )
        
        await query.edit_message_text(f"✅ {format_type.title()} downloaded successfully!")
        
    except Exception as e:
        logger.error(f"Error in download process: {e}")
        await query.edit_message_text(f"❌ An error occurred: {str(e)}")

    finally:
        # Clean up temporary files
        try:
            if filepath and os.path.exists(filepath):
                # Get temp directory from filepath
                temp_dir = os.path.dirname(filepath)

                os.remove(filepath)
                logger.info(f"🗑️ Deleted file: {os.path.basename(filepath)}")

                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"🗑️ Deleted temp directory: {os.path.basename(temp_dir)}")

        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to test URL detection"""
    if not context.args:
        await update.message.reply_text("Usage: /debug <url>")
        return
    
    url = context.args[0]
    
    # Test URL parsing
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        
        is_supported = bot.is_supported_url(url)
        platform = bot.get_platform_name(url) if is_supported else "Unknown"
        
        debug_info = f"""
🔍 URL Debug Info

Original URL: {url}
Domain: {domain}
Is Supported: {'✅ Yes' if is_supported else '❌ No'}
Detected Platform: {platform}

Supported Platforms:
{', '.join(bot.supported_platforms.keys())}
        """
        
        await update.message.reply_text(debug_info)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error parsing URL: {e}")

async def test_soundcloud(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test SoundCloud functionality"""
    test_url = "https://soundcloud.com/vgkey464/wvnouiam45kp"
    
    await update.message.reply_text("🧪 Testing SoundCloud detection...")
    
    is_supported = bot.is_supported_url(test_url)
    platform = bot.get_platform_name(test_url)
    
    result = f"""
🧪 SoundCloud Test Results

Test URL: {test_url}
Is Supported: {'✅ Yes' if is_supported else '❌ No'}
Detected Platform: {platform}

Your URL should work if:
• Domain contains 'soundcloud.com'
• URL starts with https://
• Track is public and available
    """
    
    await update.message.reply_text(result)

async def youtube_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provide YouTube download tips and test URLs"""
    tips_text = """
🎯 YouTube Download Tips

✅ VIDEOS THAT USUALLY WORK:
• Educational content (tutorials, lectures)
• Music videos from smaller artists
• Older videos (6+ months old)
• Regular videos (not Shorts)
• Videos under 10 minutes

❌ VIDEOS THAT OFTEN FAIL:
• Recent viral videos
• YouTube Shorts
• Age-restricted content
• Private/unlisted videos
• Live streams

🧪 TEST THESE WORKING URLS:
• https://www.youtube.com/watch?v=dQw4w9WgXcQ
• https://www.youtube.com/watch?v=jNQXAC9IVRw
• https://www.youtube.com/watch?v=9bZkp7q19f0

💡 PRO TIPS:
• Wait 30 seconds between YouTube requests
• Use full youtube.com URLs (not youtu.be)
• Try different videos if one doesn't work
• Educational channels work better than entertainment

🔄 The bot now tries multiple download strategies automatically!
    """
    
    await update.message.reply_text(tips_text)

async def test_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test YouTube with a known working video"""
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    
    await update.message.reply_text("🧪 Testing YouTube with a classic video...")
    
    # Simulate the URL handler
    platform = bot.get_platform_name(test_url)
    user_id = update.message.from_user.id
    url_id = bot.store_url(test_url, user_id)
    
    keyboard = [
        [
            InlineKeyboardButton("🎥 Test Video", callback_data=f"video|{url_id}"),
            InlineKeyboardButton("🎵 Test Audio", callback_data=f"audio|{url_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 Testing: {platform}\n\nChoose format to test:",
        reply_markup=reply_markup
    )

async def instagram_advanced_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provide advanced Instagram download tips"""
    tips_text = """
🔥 Advanced Instagram Tips

🎯 HIGH SUCCESS RATE CONTENT:
• Business/Creator accounts (public)
• Educational content posts
• Tutorial/How-to posts  
• Posts older than 2 weeks
• Non-viral content (< 100k likes)

📱 URL OPTIMIZATION:
✅ Use: instagram.com/p/ABC123/
✅ Use: instagram.com/reel/XYZ456/
❌ Avoid: Stories, IGTV, Live videos

🧪 TESTING STRATEGY:
1. Try oldest posts first
2. Educational accounts work best
3. Business accounts > Personal accounts
4. Non-English content sometimes works better
5. Lower engagement posts have higher success

⚡ BOT FEATURES:
• Multiple download strategies
• Instagram app user agent simulation
• Smart retry with delays
• Multiple browser fingerprints

📊 REALISTIC EXPECTATIONS:
• Regular posts: ~40-50% success
• Reels: ~25-35% success  
• Stories: ~5-10% success
• Recent viral content: ~10-20% success

💡 PRO TIP: The bot tries multiple methods automatically!
Each attempt uses different techniques to bypass restrictions.
    """
    
    await update.message.reply_text(tips_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    help_text = f"""
🆘 Help - Media Downloader Bot

Commands:
• /start - Show welcome message
• /help - Show this help message
• /debug <url> - Test URL detection
• /test_soundcloud - Test SoundCloud functionality
• /youtube_tips - Get YouTube download tips
• /instagram_tips - Get Instagram download tips
• /test_youtube - Test YouTube with working video

How to download:
1. Copy a URL from supported platforms
2. Send it to this bot
3. Choose video or audio format
4. Wait for download and upload

Supported Platforms:
• YouTube - Videos and audio
• Twitter/X - Videos and GIFs
• Instagram - Posts and stories
• TikTok - Videos
• SoundCloud - Audio tracks
• Facebook - Videos
• Vimeo - Videos

Limitations:
• Maximum file size: {max_size_mb}MB
• Audio is converted to MP3 format
• Some private or age-restricted content may not work

Tips:
• For YouTube, you can use both youtube.com and youtu.be links
• Instagram stories require the full URL
• Some platforms may have regional restrictions
    """
    
    await update.message.reply_text(help_text)

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("❌ Bot token not configured!")
        return
    
    logger.info("🤖 Initializing Telegram Media Downloader Bot...")
    logger.info(f"📁 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"📏 Max file size: {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("test_soundcloud", test_soundcloud))
    application.add_handler(CommandHandler("youtube_tips", youtube_tips))
    application.add_handler(CommandHandler("instagram_tips", instagram_advanced_tips))
    application.add_handler(CommandHandler("test_youtube", test_youtube))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_format_selection))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("🚀 Bot is running! Send /start to begin.")
    logger.info("📱 Ready to download media from supported platforms!")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()
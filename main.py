import os
import time
import asyncio
import logging
import tempfile
from collections import defaultdict, deque
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import yt_dlp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, per_minute=5):
        self.per_minute = per_minute
        self.user_requests = defaultdict(deque)
    
    def check(self, user_id):
        now = time.time()
        user_queue = self.user_requests[user_id]
        
        # Remove old requests
        while user_queue and user_queue[0] < now - 60:
            user_queue.popleft()
        
        if len(user_queue) >= self.per_minute:
            return False
        
        user_queue.append(now)
        return True

class MediaBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_TOKEN')
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN not found in .env file")
        self.temp_dir = tempfile.mkdtemp()
        self.rate_limiter = RateLimiter(per_minute=5)
        
        # Platform-specific configurations
        self.platform_configs = {
            'youtube': {
                'format': 'best[height<=720]/best',
                'max_filesize': 50000000,  # 50MB
            },
            'soundcloud': {
                'format': 'best',
                'extractaudio': True,
                'audioformat': 'mp3',
            },
            'twitter': {
                'format': 'best',
            },
        }
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = """
🎬 **Media Download Bot**

Send me URLs from:
- YouTube
- SoundCloud  
- Twitter/X
- Instagram

⚠️ Limits: 5 downloads/minute, 50MB max
        """
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def download_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        # Check if message contains a URL
        if not any(x in text for x in ['http://', 'https://', 'www.', 'youtu.be', 'youtube.com', 'soundcloud.com', 'x.com', 'twitter.com', 'instagram.com']):
            # Not a URL, ignore the message
            return
        
        # Extract URL from message (might contain other text)
        import re
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text)
        
        if not urls:
            # No valid URL found, ignore
            return
            
        url = urls[0]  # Process first URL found
        
        # Rate limiting
        if not self.rate_limiter.check(user_id):
            try:
                await update.message.reply_text("⏳ Rate limit: wait 1 minute")
            except:
                pass  # Ignore if we can't send the rate limit message
            return
        
        # Validate URL
        platform = self._detect_platform(url)
        if not platform:
            # In groups, don't respond to unsupported URLs
            if update.message.chat.type != 'private':
                return
            await update.message.reply_text("❌ Unsupported URL")
            return
        
        msg = await update.message.reply_text(f"⏳ Downloading from {platform}...")
        
        try:
            # Get platform config
            config = self.platform_configs.get(platform, {})
            
            ydl_opts = {
                'outtmpl': os.path.join(self.temp_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                **config
            }
            
            # Download
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._download_sync, url, ydl_opts, platform
            )
            
            if result['success']:
                await self._send_file(update, context, result, msg)
            else:
                await msg.edit_text(f"❌ {result['error']}")
                
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            await msg.edit_text("❌ Download failed")
    
    def _download_sync(self, url, ydl_opts, platform):
        try:
            # Add additional options for server environments
            if platform == 'youtube':
                # Try multiple methods in order
                proxy = os.getenv('HTTP_PROXY') or os.getenv('SOCKS_PROXY')
                
                if proxy:
                    ydl_opts['proxy'] = proxy
                    logger.info(f"Using proxy: {proxy}")
                elif os.path.exists('cookies.txt'):
                    ydl_opts['cookiefile'] = 'cookies.txt'
                    logger.info("Using cookies.txt for YouTube")
                
                ydl_opts.update({
                    'format': 'best[height<=720]/best',
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'referer': 'https://www.youtube.com/',
                    'sleep_interval': 1,
                    'max_sleep_interval': 3,
                    'nocheckcertificate': True,
                    'geo_bypass': True,
                })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info
                info = ydl.extract_info(url, download=False)
                if not info:
                    return {'success': False, 'error': 'Cannot access content'}
                
                # Platform-specific checks
                if platform == 'youtube':
                    duration = info.get('duration', 0)
                    if duration > 1800:  # 30 minutes
                        return {'success': False, 'error': 'Video too long (max 30 min)'}
                
                # Download
                ydl.download([url])
                
                # Find file
                title = info.get('title', 'Unknown')[:50]  # Limit title length
                uploader = info.get('uploader', '')
                
                for file in os.listdir(self.temp_dir):
                    if not file.startswith('.'):
                        file_path = os.path.join(self.temp_dir, file)
                        file_size = os.path.getsize(file_path)
                        
                        # Check size
                        if file_size > 50 * 1024 * 1024:
                            os.unlink(file_path)
                            return {'success': False, 'error': 'File too large (>50MB)'}
                        
                        # Determine type
                        ext = os.path.splitext(file)[1].lower()
                        if ext in ['.mp4', '.webm', '.mov', '.avi']:
                            file_type = 'video'
                        elif ext in ['.mp3', '.m4a', '.wav', '.ogg']:
                            file_type = 'audio'
                        elif ext in ['.jpg', '.jpeg', '.png', '.gif']:
                            file_type = 'photo'
                        else:
                            file_type = 'document'
                        
                        return {
                            'success': True,
                            'path': file_path,
                            'title': title,
                            'uploader': uploader,
                            'type': file_type,
                            'platform': platform
                        }
                
                return {'success': False, 'error': 'Download completed but file not found'}
                
        except Exception as e:
            error = str(e)[:200]
            
            # Common error messages
            if 'private' in error.lower() or 'login' in error.lower():
                return {'success': False, 'error': 'Content is private or requires login'}
            elif '404' in error or 'not found' in error.lower():
                return {'success': False, 'error': 'Content not found or deleted'}
            elif 'copyright' in error.lower():
                return {'success': False, 'error': 'Content blocked due to copyright'}
            else:
                return {'success': False, 'error': f'Platform restrictions or error'}
    
    async def _send_file(self, update, context, result, msg):
        try:
            await msg.edit_text("📤 Uploading...")
            
            caption = f"✅ {result['title']}\n"
            if result['uploader']:
                caption += f"👤 {result['uploader']}\n"
            caption += f"📍 {result['platform'].title()}"
            
            with open(result['path'], 'rb') as f:
                if result['type'] == 'video':
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=f,
                        caption=caption,
                        supports_streaming=True
                    )
                elif result['type'] == 'audio':
                    await context.bot.send_audio(
                        chat_id=update.effective_chat.id,
                        audio=f,
                        caption=caption,
                        title=result['title']
                    )
                elif result['type'] == 'photo':
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=f,
                        caption=caption
                    )
                else:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        caption=caption
                    )
            
            await msg.delete()
            
        except Exception as e:
            logger.error(f"Send error: {e}")
            await msg.edit_text("❌ Failed to send file")
        finally:
            # Cleanup
            if os.path.exists(result['path']):
                os.unlink(result['path'])
    
    def _detect_platform(self, url):
        try:
            # Clean the URL
            url = url.strip()
            
            # Check for platform patterns anywhere in the URL
            platforms = {
                'youtube': ['youtube.com', 'youtu.be', 'm.youtube'],
                'soundcloud': ['soundcloud.com'],
                'twitter': ['twitter.com', 'x.com', 't.co'],
                'instagram': ['instagram.com', 'instagr.am']
            }
            
            for platform, domains in platforms.items():
                if any(d in url.lower() for d in domains):
                    return platform
            return None
        except:
            return None
    
    def run(self):
        app = Application.builder().token(self.token).build()
        
        # Add error handler
        async def error_handler(update, context):
            logger.error(f"Exception: {context.error}")
            if update and hasattr(update, 'effective_message'):
                try:
                    await update.effective_message.reply_text(
                        "❌ An error occurred. Please try again later."
                    )
                except:
                    pass  # Can't send message, probably rate limited
        
        app.add_error_handler(error_handler)
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.download_media))
        logger.info("Bot started...")
        app.run_polling()

if __name__ == '__main__':
    bot = MediaBot()
    bot.run()
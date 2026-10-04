import requests
from urllib.parse import urljoin
import time
import os
import re
import threading
import concurrent.futures
import argparse
import signal
from datetime import datetime

# ============================================================
#  CONFIGURATION
# ============================================================

FRONTEND_URL = "http://10.15.223.139/"

# 🔥 DISCORD WEBHOOK
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1553665610666344591/9Au8Z7WU3gEbzD9T-fngsSJkvp8RziyO1u2EpTYW6tEWld6pWz2X6VY6wtlnTSER-WXi"

MAX_WORKERS = 16
TIMEOUT = 8

PREVIEW_START = 1
PREVIEW_END = 43500

EXTENSIONS = (
    '.mp4', '.mkv', '.iso', '.mp3', '.avi', '.mpg', '.flac', '.wav',
    '.exe', '.zip', '.rar', '.7z', '.m4a', '.m4v', '.wmv', '.mov',
    '.mpeg', '.bin', '.cue', '.img', '.nrg', '.mp2', '.ogg', '.wma',
    '.aac', '.mka', '.flv', '.webm', '.3gp', '.ts', '.m2ts', '.mts'
)

URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+\.(?:' + '|'.join(ext.lstrip('.') for ext in EXTENSIONS) + r')',
    re.IGNORECASE
)

# ============================================================
#  DISCORD SENDER (No Disk Save)
# ============================================================

class DiscordSender:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
        self.lock = threading.Lock()

    def send_content(self, content, filename, category, count):
        """Send content directly from memory — NO DISK SAVE"""
        # Check size limit (Discord = 8MB for free webhooks)
        content_bytes = content.encode('utf-8')
        size_mb = len(content_bytes) / (1024 * 1024)
        
        if size_mb > 7.5:
            # Too big — split and send in chunks
            print(f"   ⚠️ File too big ({size_mb:.2f}MB), splitting...")
            return self._send_split(content, filename, category, count)
        
        try:
            files = {
                'file': (filename, content_bytes, 'text/plain')
            }
            payload = {
                'content': f"📂 **{category.upper()}** — `{count}` links\n"
                           f"📁 File: `{filename}`\n"
                           f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            }
            
            with self.lock:
                r = requests.post(self.webhook_url, data=payload, files=files, timeout=30)
            
            if r.status_code in (200, 204):
                print(f"   ✅ Sent: {filename} ({count} links, {size_mb:.2f}MB)")
                return True
            else:
                print(f"   ❌ Discord error: {r.status_code} - {r.text[:100]}")
                return False
        except Exception as e:
            print(f"   ❌ Discord exception: {e}")
            return False

    def _send_split(self, content, filename, category, count):
        """Split large content into chunks"""
        lines = content.split('\n')
        chunk_size = 5000  # lines per chunk
        chunks = [lines[i:i+chunk_size] for i in range(0, len(lines), chunk_size)]
        
        for idx, chunk in enumerate(chunks, 1):
            chunk_content = '\n'.join(chunk)
            part_name = f"{filename.replace('.txt', '')}_part{idx}.txt"
            
            try:
                files = {'file': (part_name, chunk_content.encode('utf-8'), 'text/plain')}
                payload = {
                    'content': f"📂 **{category.upper()}** (Part {idx}/{len(chunks)})\n"
                               f"📁 File: `{part_name}`"
                }
                
                with self.lock:
                    r = requests.post(self.webhook_url, data=payload, files=files, timeout=30)
                    time.sleep(1)  # Rate limit protection
                
                if r.status_code in (200, 204):
                    print(f"   ✅ Sent part {idx}/{len(chunks)}: {part_name}")
                else:
                    print(f"   ❌ Part {idx} failed: {r.status_code}")
            except Exception as e:
                print(f"   ❌ Part {idx} exception: {e}")
        
        return True

    def send_message(self, message):
        """Send plain text message to Discord"""
        try:
            with self.lock:
                r = requests.post(self.webhook_url, json={'content': message}, timeout=15)
            return r.status_code in (200, 204)
        except Exception:
            return False


# ============================================================
#  SCRAPER (No Disk Save)
# ============================================================

class FastScraper:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.all_links = {
            'movies': set(),
            'games': set(),
            'music': set(),
            'software': set(),
            'tvshows': set(),
            'wallpapers': set(),
            'videos': set(),
            'other': set()
        }
        self.lock = threading.Lock()
        self.running = True
        self.processed = 0
        self.found_count = 0
        self.start_time = time.time()
        self.last_update = time.time()
        
        # Discord sender
        self.discord = DiscordSender(DISCORD_WEBHOOK)
        
        # Session
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
            max_retries=0
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        signal.signal(signal.SIGINT, self.signal_handler)

    def signal_handler(self, sig, frame):
        print("\n\n⚠️ Stopping... Sending collected links to Discord...")
        self.running = False

    def categorize(self, url):
        u = url.lower()
        if '/englishmovies/' in u or '/hindimovies/' in u or 'hollywood' in u or 'bollywood' in u or 'wrestling' in u:
            return 'movies'
        elif '/games' in u or '/racing/' in u or '/action/' in u:
            return 'games'
        elif '/music/' in u or '/mp3/' in u or '/song/' in u:
            return 'music'
        elif '/software' in u:
            return 'software'
        elif '/tvshow' in u or '/season' in u or '/series' in u:
            return 'tvshows'
        elif '/wallpaper' in u or '/image/' in u or '/photo/' in u:
            return 'wallpapers'
        elif '/video/' in u:
            return 'videos'
        return 'other'

    def extract_links(self, html):
        found = set()
        for match in URL_PATTERN.finditer(html):
            found.add(match.group(0))
        
        src_pattern = re.compile(
            r'src=["\']([^"\']+\.(?:' + '|'.join(ext.lstrip('.') for ext in EXTENSIONS) + r'))["\']',
            re.IGNORECASE
        )
        for match in src_pattern.finditer(html):
            url = match.group(1)
            if not url.startswith('http'):
                url = urljoin(FRONTEND_URL, url)
            found.add(url)
        
        return found

    def build_category_content(self, category):
        """Build content in memory — NO DISK SAVE"""
        links = self.all_links[category]
        if not links:
            return None
        
        content = "="*80 + "\n"
        content += f"  🎯 BEAT BOX - {category.upper()}\n"
        content += f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += f"  📊 Total Links: {len(links)}\n"
        content += "="*80 + "\n\n"
        
        for link in sorted(links):
            content += f"{link}\n"
        
        content += "\n" + "="*80 + "\n"
        return content

    def send_category(self, category):
        """Send a single category to Discord (from memory)"""
        content = self.build_category_content(category)
        if content is None:
            return
        
        count = len(self.all_links[category])
        filename = f"BEATBOX_{category.upper()}.txt"
        
        print(f"\n   📤 Sending {category.upper()} ({count} links)...")
        self.discord.send_content(content, filename, category, count)

    def send_all_categories(self, final=True):
        """Send all categories to Discord"""
        print("\n" + "="*60)
        print(f"📤 Sending {'FINAL' if final else 'INTERMEDIATE'} files to Discord...")
        print("="*60)
        
        for category in ['movies', 'games', 'music', 'software', 'tvshows', 'wallpapers', 'videos', 'other']:
            if self.all_links[category]:
                self.send_category(category)

    def scrape_one(self, movie_id):
        if not self.running:
            return
        
        url = f"{FRONTEND_URL}classic/moviepreview-{movie_id}.html"
        
        try:
            response = self.session.get(url, timeout=TIMEOUT)
            
            if response.status_code == 200:
                links = self.extract_links(response.text)
                
                if links:
                    with self.lock:
                        for link in links:
                            category = self.categorize(link)
                            if link not in self.all_links[category]:
                                self.all_links[category].add(link)
                                self.found_count += 1
                    
                    if self.verbose:
                        print(f"✅ ID {movie_id}: {len(links)} link(s)")
                        
        except Exception:
            pass
        
        with self.lock:
            self.processed += 1
            now = time.time()
            if now - self.last_update >= 1.0:
                self.print_progress()
                self.last_update = now

    def print_progress(self):
        if not self.running:
            return
        
        total = PREVIEW_END - PREVIEW_START + 1
        percent = (self.processed / total) * 100
        elapsed = time.time() - self.start_time
        
        if self.processed > 0:
            speed = self.processed / elapsed
            eta_seconds = (total - self.processed) / speed if speed > 0 else 0
            eta_min = int(eta_seconds // 60)
            eta_sec = int(eta_seconds % 60)
            
            bar_length = 30
            filled = int(bar_length * self.processed / total)
            bar = '█' * filled + '░' * (bar_length - filled)
            
            print(f"\r[{bar}] {percent:5.1f}% | "
                  f"{self.processed}/{total} | "
                  f"🔗 {self.found_count} links | "
                  f"⚡ {speed:.1f}/s | "
                  f"⏱️ ETA: {eta_min}m {eta_sec}s   ", end='', flush=True)

    def scrape_all(self, start_id, end_id):
        global PREVIEW_START, PREVIEW_END
        PREVIEW_START = start_id
        PREVIEW_END = end_id
        
        total = end_id - start_id + 1
        
        print("""
╔══════════════════════════════════════════════════════════╗
║   🎯 BEAT BOX - MEMORY-ONLY SCRAPER v5.0                ║
║   No Disk Save • Only Discord                           ║
╚══════════════════════════════════════════════════════════╝
        """)
        print(f"📡 Target: {FRONTEND_URL}classic/moviepreview-*.html")
        print(f"🔢 Range: {start_id} to {end_id} ({total:,} pages)")
        print(f"⚡ Threads: {MAX_WORKERS}")
        print(f"💾 Disk Save: ❌ DISABLED")
        print(f"🔔 Discord: ✅ ENABLED")
        print(f"⏱️  Started: {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        print("Press Ctrl+C anytime to stop & send to Discord\n")
        
        # Send start notification
        self.discord.send_message(
            f"🚀 **Beat Box Scraper Started**\n"
            f"📊 Range: `{start_id}` to `{end_id}`\n"
            f"⚡ Threads: `{MAX_WORKERS}`\n"
            f"💾 Disk: ❌ Disabled\n"
            f"⏰ Started: `{datetime.now().strftime('%H:%M:%S')}`"
        )
        
        # Interval-based sending (har 10 minute baad)
        last_send_time = time.time()
        SEND_INTERVAL = 600  # 10 minutes
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for movie_id in range(start_id, end_id + 1):
                if not self.running:
                    break
                futures.append(executor.submit(self.scrape_one, movie_id))
            
            while futures:
                done, futures = concurrent.futures.wait(
                    futures, timeout=10,
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                
                now = time.time()
                if now - last_send_time >= SEND_INTERVAL:
                    print(f"\n⏰ Interval reached! Sending intermediate results...")
                    self.send_all_categories(final=False)
                    last_send_time = now
                
                if not self.running:
                    break
        
        print()
        self.print_progress()
        print()

    def save_links(self, final=True):
        """Send all collected links to Discord (no disk save)"""
        print("\n" + "="*60)
        print("📤 Sending ALL results to Discord...")
        
        total = sum(len(v) for v in self.all_links.values())
        elapsed = time.time() - self.start_time
        
        # Send each category separately
        self.send_all_categories(final=final)
        
        # Build & send master file (in memory)
        try:
            master_content = "="*80 + "\n"
            master_content += "  🎯 BEAT BOX - ALL LINKS\n"
            master_content += f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            master_content += f"  ⏱️  Total Time: {int(elapsed//60)}m {int(elapsed%60)}s\n"
            master_content += f"  📊 Pages Scanned: {self.processed}\n"
            master_content += "="*80 + "\n\n"
            
            for category in ['movies', 'games', 'music', 'software', 'tvshows', 'wallpapers', 'videos', 'other']:
                links = self.all_links[category]
                if links:
                    master_content += f"\n{'█'*60}\n"
                    master_content += f"  📂 {category.upper()} ({len(links)} links)\n"
                    master_content += f"{'█'*60}\n\n"
                    for link in sorted(links):
                        master_content += f"{link}\n"
                    master_content += "\n"
            
            master_content += "\n" + "="*80 + "\n"
            master_content += f"  ✅ TOTAL LINKS: {total}\n"
            master_content += "="*80 + "\n"
            
            print(f"\n📤 Sending master file...")
            self.discord.send_content(master_content, "BEATBOX_ALL_LINKS.txt", "ALL_CATEGORIES", total)
            
            # Send summary message
            self.discord.send_message(
                f"✅ **Beat Box Scraper Completed!**\n\n"
                f"📊 **Total Links:** `{total}`\n"
                f"📄 **Pages Scanned:** `{self.processed}`\n"
                f"⏱️ **Time:** `{int(elapsed//60)}m {int(elapsed%60)}s`\n"
                f"📁 **Files Sent:** `{len([c for c in self.all_links if self.all_links[c]])}` categories\n"
                f"💾 **Disk Save:** ❌ Disabled\n\n"
                f"🎉 **All Done!**"
            )
            
        except Exception as e:
            print(f"\n❌ Discord error: {e}")
        
        print(f"\n✅ DONE!")
        print(f"📊 Total links: {total}")
        print(f"📤 All files sent to Discord!")
        print(f"⏱️  Time: {int(elapsed//60)}m {int(elapsed%60)}s")
        return True

    def run(self, start_id, end_id):
        try:
            self.scrape_all(start_id, end_id)
            return self.save_links(final=True)
        except KeyboardInterrupt:
            print("\n\n⚠️ Interrupted! Sending to Discord...")
            return self.save_links(final=True)
        except Exception as e:
            print(f"\n❌ Error: {e}")
            return self.save_links(final=True)


# ============================================================
#  MAIN
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description='Beat Box Memory-Only Scraper v5.0')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show every found link')
    parser.add_argument('--start', type=int, default=PREVIEW_START,
                       help=f'Start ID (default: {PREVIEW_START})')
    parser.add_argument('--end', type=int, default=PREVIEW_END,
                       help=f'End ID (default: {PREVIEW_END})')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    scraper = FastScraper(verbose=args.verbose)
    scraper.run(start_id=args.start, end_id=args.end)

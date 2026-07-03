# Discord YouTube Music Bot (Python / discord.py)

บอท Discord สำหรับเล่นเสียงจาก YouTube URL ด้วย slash commands ของ `discord.py`:

```text
User
  |
/play <youtube_url>
  |
Discord Bot
  |
Validate URL
  |
yt-dlp Python API
  |
Extract audio information + direct audio URL
  |
FFmpeg
  |
Decode audio
  |
Discord Voice Client
```

## สำคัญเรื่อง Token

Token ที่เคยส่งในแชตควรถูกมองว่ารั่วแล้ว ให้ reset token ใน Discord Developer Portal ก่อนใช้งานจริง แล้วนำ token ใหม่ไปใส่ใน Replit Secrets ชื่อ `DISCORD_TOKEN`

## ไฟล์ที่ต้องแก้หรือเติมค่า

แนะนำให้ใช้ Replit Secrets:

- `DISCORD_TOKEN` ต้องใส่ token ใหม่ของบอท
- `TEST_GUILD_ID` ใส่ Server ID สำหรับทดสอบ slash command ให้ sync เร็ว
- `FFMPEG_EXECUTABLE` ปกติไม่ต้องใส่ เพราะโค้ดใช้ `imageio-ffmpeg` เป็น fallback ให้แล้ว
- `MAX_QUEUE_SIZE` ค่าเริ่มต้น `50`
- `MAX_VIDEO_SECONDS` ค่าเริ่มต้น `10800` วินาที หรือ 3 ชั่วโมง

ใน `main.py` มี comment กำกับค่าพวกนี้ไว้ด้านบนไฟล์แล้ว

## วิธีรันบน Replit

1. สร้าง Repl แบบ Python
2. อัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้เข้า Replit
3. เปิด Secrets แล้วเพิ่ม:
   - Key: `DISCORD_TOKEN`
   - Value: token ใหม่ของบอท
4. เพิ่ม `TEST_GUILD_ID` เป็น Server ID ตอนทดสอบ ถ้าต้องการ
5. กด Run

ถ้า Replit ไม่ติดตั้ง package อัตโนมัติ ให้รันใน Shell:

```bash
pip install -r requirements.txt
```

## Invite Bot

ตอนเชิญบอทเข้า server ให้เลือก scope:

- `bot`
- `applications.commands`

Permissions ที่ควรมี:

- View Channels
- Send Messages
- Connect
- Speak

## คำสั่ง

- `/play <url>` เล่นหรือเพิ่มเพลงจาก YouTube URL
- `/pause` พักเพลง
- `/resume` เล่นต่อ
- `/skip` ข้ามเพลง
- `/stop` หยุดและล้างคิว
- `/queue` ดูคิว
- `/nowplaying` ดูเพลงปัจจุบัน
- `/leave` ออกจากห้องเสียง

## หมายเหตุ

- ถ้าใช้ `TEST_GUILD_ID` คำสั่งจะขึ้นเร็วใน server นั้น
- ถ้าไม่ใช้ `TEST_GUILD_ID` คำสั่ง global อาจใช้เวลาสักพักกว่าจะปรากฏ
- ถ้ารันบน VS Code แล้วเจอ `ffmpeg was not found` ให้รัน `pip install -r requirements.txt` อีกครั้ง เพื่อให้ติดตั้ง `imageio-ffmpeg`
- YouTube เปลี่ยนระบบบ่อย ถ้าดึงเพลงไม่ได้ ให้ update `yt-dlp` ก่อน

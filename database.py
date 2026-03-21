import aiosqlite

DB_NAME = "kinobot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Create movies table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_code TEXT UNIQUE,
                file_id TEXT,
                caption TEXT
            )
        """)
        
        # Create users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM movies") as cursor:
            movies_count = (await cursor.fetchone())[0]
        return users_count, movies_count

async def get_all_movies():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT movie_code FROM movies") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def add_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def save_movie(movie_code: str, file_id: str, caption: str):
    async with aiosqlite.connect(DB_NAME) as db:
        # Save or Update movie mapping
        await db.execute("""
            INSERT INTO movies (movie_code, file_id, caption)
            VALUES (?, ?, ?)
            ON CONFLICT(movie_code) DO UPDATE SET
                file_id = excluded.file_id,
                caption = excluded.caption
        """, (movie_code, file_id, caption))
        await db.commit()

async def get_movie(movie_code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT file_id, caption FROM movies WHERE movie_code = ?", (movie_code,)) as cursor:
            return await cursor.fetchone()

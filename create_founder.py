import os
import ssl
import uuid
import bcrypt
import dotenv
import pg8000
from urllib.parse import urlparse

dotenv.load_dotenv('.env.local')
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("DATABASE_URL not set")
    exit(1)

parsed = urlparse(DATABASE_URL)
if parsed.scheme != 'postgresql':
    print("Invalid scheme")
    exit(1)
user = parsed.username
password = parsed.password
host = parsed.hostname
port = parsed.port or 5432
dbname = parsed.path.lstrip('/')

# Create default SSL context
ssl_context = ssl.create_default_context()

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

email = 'founder@closeai.io'
plain = 'Founder@123'
name = 'Founder'

conn = pg8000.connect(
    user=user,
    password=password,
    host=host,
    port=port,
    database=dbname,
    ssl_context=ssl_context
)
try:
    with conn.cursor() as c:
        c.execute('SELECT id FROM users WHERE email = %s', (email,))
        if c.fetchone():
            print('Founder user already exists.')
            exit(0)
        uid = str(uuid.uuid4())
        hashed = hash_password(plain)
        c.execute('''
            INSERT INTO users (id, email, password_hash, name, is_founder)
            VALUES (%s, %s, %s, %s, %s)
        ''', (uid, email, hashed, name, True))
        conn.commit()
        print(f'Founder user created: {email}')
        print(f'User ID: {uid}')
finally:
    conn.close()

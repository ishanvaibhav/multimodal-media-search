import os
import sqlite3
import firebase_admin
from firebase_admin import credentials, auth

# Initialize Firebase Admin
# NOTE: Set GOOGLE_APPLICATION_CREDENTIALS env var to your service account key JSON
if not firebase_admin._apps:
    firebase_admin.initialize_app()

DB_PATH = os.getenv("DB_PATH", "users.db")

ADMINS = [
    {"email": "ishanvaibhav@pixellab.media", "phone": "+19259881072", "password": "12345678", "role": "ADMIN"},
    {"email": "shreekant@pixellab.media", "phone": "+19259881072", "password": "12345678", "role": "ADMIN"},
    {"email": "nehasharma@pixellab.media", "phone": "+19259881072", "password": "12345678", "role": "ADMIN"},
]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)
    conn.commit()
    return conn

def seed_users():
    conn = init_db()
    cursor = conn.cursor()

    for admin in ADMINS:
        email = admin["email"]
        password = admin["password"]
        phone = admin["phone"]
        role = admin["role"]

        # Create user in Firebase
        try:
            user = auth.get_user_by_email(email)
            print(f"User {email} already exists in Firebase (UID: {user.uid}).")
        except firebase_admin.auth.UserNotFoundError:
            try:
                user = auth.create_user(
                    email=email,
                    password=password,
                    phone_number=phone,
                    email_verified=True
                )
                print(f"Created user {email} in Firebase with UID: {user.uid}.")
                
                # Set custom claims for role
                auth.set_custom_user_claims(user.uid, {"role": role})
            except Exception as e:
                print(f"Failed to create user {email} in Firebase: {e}")
                continue

        # Insert user into SQLite
        try:
            cursor.execute(
                "INSERT INTO users (uid, email, role, status) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET role=excluded.role, status=excluded.status",
                (user.uid, email, role, "ACTIVE")
            )
            print(f"Saved {email} to local SQLite users table.")
        except Exception as e:
            print(f"Failed to save {email} to SQLite: {e}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    print("Starting Firebase & SQLite seed...")
    seed_users()
    print("Seed complete.")

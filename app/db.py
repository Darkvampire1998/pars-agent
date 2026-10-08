import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,login TEXT NOT NULL,server TEXT NOT NULL,key_hash TEXT UNIQUE NOT NULL,config TEXT NOT NULL,baseline REAL NOT NULL,day TEXT,day_start REAL,last_seen REAL,snapshot TEXT,instance_id TEXT,UNIQUE(login,server));
CREATE TABLE IF NOT EXISTS markets(account_id TEXT,symbol TEXT,timeframe TEXT,data TEXT NOT NULL,PRIMARY KEY(account_id,symbol,timeframe));
CREATE TABLE IF NOT EXISTS ideas(id TEXT PRIMARY KEY,account_id TEXT,symbol TEXT,timeframe TEXT,candle INTEGER,strategy TEXT,data TEXT,created REAL,UNIQUE(account_id,symbol,timeframe,candle,strategy));
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,account_id TEXT,idea_id TEXT UNIQUE,status TEXT,data TEXT,created REAL,updated REAL,result TEXT);
CREATE TABLE IF NOT EXISTS deals(account_id TEXT,ticket TEXT,time REAL,symbol TEXT,data TEXT,PRIMARY KEY(account_id,ticket));
CREATE TABLE IF NOT EXISTS telegram(user_id TEXT PRIMARY KEY,token TEXT,chat_id TEXT,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,user_id TEXT,message TEXT,status TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_try REAL,last_error TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id TEXT,account_id TEXT,event TEXT,data TEXT,created REAL);
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,count INTEGER,reset REAL);
CREATE TABLE IF NOT EXISTS connections(account_id TEXT PRIMARY KEY,password TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'waiting',updated REAL,checked REAL);
CREATE TABLE IF NOT EXISTS decisions(account_id TEXT PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS safety(account_id TEXT PRIMARY KEY,day TEXT,reason TEXT,updated REAL);
CREATE INDEX IF NOT EXISTS idx_ideas ON ideas(account_id,created);
CREATE INDEX IF NOT EXISTS idx_outbox ON outbox(status,next_try);
"""

def connect():
    path = os.environ.get("DB_PATH", "data/agent.sqlite")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def initialize():
    with connect() as db:
        db.executescript(SCHEMA)

@contextmanager
def transaction():
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

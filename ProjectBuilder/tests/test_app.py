import sqlite3


def test_create_and_query_projects_table():
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            prompt TEXT,
            backend_code TEXT,
            frontend_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'created',
            port INTEGER,
            framework TEXT DEFAULT 'react'
        )
    ''')
    conn.commit()
    cursor.execute('INSERT INTO projects (name, description, prompt, backend_code, frontend_code) VALUES (?, ?, ?, ?, ?)',
                   ('p1', 'desc', 'prompt', 'bcode', 'fcode'))
    pid = cursor.lastrowid
    conn.commit()
    cursor.execute('SELECT name, backend_code FROM projects WHERE id = ?', (pid,))
    row = cursor.fetchone()
    assert row[0] == 'p1'
    assert row[1] == 'bcode'
    conn.close()


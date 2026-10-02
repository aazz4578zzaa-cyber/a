from datetime import datetime
from database import db_execute


async def db_add_account(owner_id, phone, api_id, api_hash, session_string, account_name, username):
    result = await db_execute(
        '''INSERT INTO accounts (owner_id, phone, api_id, api_hash, session_string, account_name, username, created_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id''',
        (owner_id, phone, api_id, api_hash, session_string, account_name, username, datetime.now().isoformat())
    )
    return result


async def db_get_accounts(owner_id):
    rows = await db_execute(
        'SELECT id, owner_id, phone, api_id, api_hash, session_string, account_name, username, created_date, is_active '
        'FROM accounts WHERE owner_id = ? AND is_active = 1 ORDER BY id DESC',
        (owner_id,), fetch=True
    )
    return rows or []


async def db_get_account(acc_id):
    row = await db_execute(
        'SELECT id, owner_id, phone, api_id, api_hash, session_string, account_name, username, created_date, is_active '
        'FROM accounts WHERE id = ?',
        (acc_id,), fetch_one=True
    )
    return row


async def db_delete_account(acc_id):
    await db_execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (acc_id,))


async def db_add_template(owner_id, title, text):
    result = await db_execute(
        '''INSERT INTO templates (owner_id, title, text, created_date)
           VALUES (?, ?, ?, ?) RETURNING id''',
        (owner_id, title, text, datetime.now().isoformat())
    )
    return result


async def db_get_templates(owner_id):
    rows = await db_execute(
        'SELECT id, owner_id, title, text, created_date FROM templates WHERE owner_id = ? ORDER BY id DESC',
        (owner_id,), fetch=True
    )
    return rows or []


async def db_get_template(tid):
    row = await db_execute(
        'SELECT id, owner_id, title, text, created_date FROM templates WHERE id = ?',
        (tid,), fetch_one=True
    )
    return row


async def db_delete_template(tid):
    await db_execute('DELETE FROM templates WHERE id = ?', (tid,))


async def db_get_settings(owner_id):
    row = await db_execute(
        'SELECT owner_id, current_account_id, current_template_id, channel_id, channel_title '
        'FROM settings WHERE owner_id = ?',
        (owner_id,), fetch_one=True
    )
    if not row:
        await db_execute(
            'INSERT INTO settings (owner_id) VALUES (?)',
            (owner_id,)
        )
        row = await db_execute(
            'SELECT owner_id, current_account_id, current_template_id, channel_id, channel_title '
            'FROM settings WHERE owner_id = ?',
            (owner_id,), fetch_one=True
        )
    return row


async def db_update_setting(owner_id, **kwargs):
    await db_get_settings(owner_id)  # اطمینان از وجود
    for key, val in kwargs.items():
        await db_execute(
            f'UPDATE settings SET {key} = ? WHERE owner_id = ?',
            (val, owner_id)
        )

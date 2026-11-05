import json
import sqlite3
from bt.article_rollback import quarantine,restore


def test_old_worker_cannot_learn_article_votes_or_apply_article_rules_after_guard():
    c=sqlite3.connect(':memory:')
    for table,field,definition in [('feedback','processed','INTEGER'),('lesson','active','INTEGER'),('memorycore','sources','TEXT')]:
        c.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY,content_type TEXT,{field} {definition}' + (',rating INTEGER' if table=='feedback' else '') + ')')
    c.executemany('INSERT INTO feedback VALUES (?,?,?,?)',[(1,'video',0,-1),(2,'article',0,-1)])
    c.executemany('INSERT INTO lesson VALUES (?,?,?)',[(1,'video',1),(2,'article',1)])
    c.executemany('INSERT INTO memorycore VALUES (?,?,?)',[(1,'video','{"1":"video-hash"}'),(2,'article','{"2":"article-hash"}')]);c.commit()
    before=[c.execute(f'SELECT * FROM {table}').fetchall() for table in ('feedback','lesson','memorycore')]
    snapshot=quarantine(c)
    assert c.execute('SELECT id FROM feedback WHERE processed=0').fetchall()==[(1,)]
    assert c.execute('SELECT id FROM feedback WHERE rating<0').fetchall()==[(1,)]
    assert c.execute('SELECT id FROM lesson WHERE active=1').fetchall()==[(1,)]
    old_core=json.loads(c.execute('SELECT sources FROM memorycore ORDER BY id DESC LIMIT 1').fetchone()[0])
    assert any(key not in {'1':'video-hash'} for key in old_core)
    restore(c,snapshot)
    assert before==[c.execute(f'SELECT * FROM {table}').fetchall() for table in ('feedback','lesson','memorycore')]

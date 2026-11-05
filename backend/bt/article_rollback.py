"""Quarantine scoped article learning before starting a pre-article worker.

The caller retains a full database backup and saves the returned reversible field
snapshot privately. Legacy workers cannot distinguish typed lessons or feedback.
"""
import json


def quarantine(connection):
    """Atomically pause article learning; retain original rows and raw evidence."""
    fields = {}
    for table in ('feedback', 'lesson', 'memorycore'):
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info({table})')}
        if 'content_type' not in columns:
            return {}
    connection.execute('BEGIN IMMEDIATE')
    try:
        fields['feedback'] = connection.execute("SELECT id, processed, rating FROM feedback WHERE content_type='article'").fetchall()
        fields['lessons'] = connection.execute("SELECT id, active FROM lesson WHERE content_type='article'").fetchall()
        fields['cores'] = connection.execute("SELECT id, sources FROM memorycore WHERE content_type='article'").fetchall()
        connection.execute("UPDATE feedback SET processed=1, rating=0 WHERE content_type='article'")
        connection.execute("UPDATE lesson SET active=0 WHERE content_type='article'")
        # An unknown source invalidates this prefix for old source-validation code;
        # original video source rules remain its safe fallback.
        connection.execute("UPDATE memorycore SET sources=? WHERE content_type='article'",
                           (json.dumps({'__article_scope_quarantine__': 'requires typed worker'}),))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return fields


def restore(connection, snapshot):
    """Restore quarantined flags after deploying a typed worker again."""
    connection.execute('BEGIN IMMEDIATE')
    try:
        for fid, processed, rating in snapshot.get('feedback', []):
            connection.execute("UPDATE feedback SET processed=?, rating=? WHERE id=? AND content_type='article'", (processed, rating, fid))
        for lid, active in snapshot.get('lessons', []):
            connection.execute("UPDATE lesson SET active=? WHERE id=? AND content_type='article'", (active, lid))
        for cid, sources in snapshot.get('cores', []):
            connection.execute("UPDATE memorycore SET sources=? WHERE id=? AND content_type='article'", (sources, cid))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

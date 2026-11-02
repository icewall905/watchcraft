"""Conservative identity gate shared by collection and report generation."""
import re
import unicodedata
from urllib.parse import unquote


def _normal(text: str) -> str:
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', text).casefold()))


def identity_names(c) -> list[str]:
    names = [c.subject or c.name]
    for alias in c.aliases or []:
        parts = alias.split()
        # Preserve multiword names; split accidentally combined numeric handles.
        if len(parts) > 1 and all(re.fullmatch(r'[\w.@-]+', p) and re.search(r'\d', p) for p in parts):
            names.extend(parts)
        else:
            names.append(alias)
    return list(dict.fromkeys(names))


def person_match(c, data: dict) -> bool:
    """A shared first name or surname is insufficient evidence of identity.

    Aliases are user-supplied identity hints, not proof that two accounts belong
    to the same person. Reports must retain that distinction.
    """
    if c.kind != 'research' or c.subject_type != 'person':
        return True
    hay = ' ' + _normal(' '.join(str(data.get(k) or '') for k in ('title', 'summary'))
                        + ' ' + unquote(str(data.get('url') or ''))) + ' '
    return any(' ' + _normal(name) + ' ' in hay
               for name in identity_names(c) if _normal(name))

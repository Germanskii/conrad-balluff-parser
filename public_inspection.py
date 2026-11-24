"""Allowlisted structural diagnostics: no cookies, scripts, form values or account text."""

import json
from pathlib import Path

from data import DataError


def save_structure(page, name: str):
    # Diagnostic output intentionally omits raw HTML, account menus, screenshots,
    # network headers and arbitrary page text. Product content is inspected separately.
    structure = page.evaluate("""() => {
        const h1 = document.querySelector('h1');
        if (!h1 || !h1.innerText.toUpperCase().includes('BALLUFF')) return null;
        const safe = e => ({tag:e.tagName,
            id: /^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(e.id) ? e.id : '',
            classes:[...e.classList].filter(c=>/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(c)),
            role:e.getAttribute('role'), children:e.children.length});
        const ancestors=[];
        for(let e=h1;e && e.tagName!=='BODY';e=e.parentElement) {
            ancestors.push({...safe(e), childStructure:[...e.children].map(safe)});
        }
        const known=new Set(['Highlights & Details','Technische Daten','Dokumente & Downloads',
            'Beschreibung','Datenblatt','Bedienungs- und Sicherheitshinweise','Alle Angebote','Abmelden']);
        const controls=[...document.querySelectorAll('a,button,h2,h3')]
            .filter(e=>known.has(e.innerText.trim())).map(safe);
        return {productTitle:h1.innerText, ancestors, knownControls:controls};
    }""")
    if structure is None:
        raise DataError(
            "No loaded Balluff product heading; no diagnostic report was saved"
        )
    root = Path(__file__).resolve().parent / "local/research"
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{name}-structure.json"
    target.write_text(
        json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved allowlisted product structure: {target}")

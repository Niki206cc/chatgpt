from pathlib import Path
import confirmation_patch as cp

# Correzioni/integrazioni categorie Postie/WordPress.
cp.CATEGORY_NAMES['valseriana'] = 'Valle Seriana'
cp.CATEGORY_NAMES['val-gandino'] = 'Val Gandino'

# Aggiunge Val Gandino alla schermata di selezione categorie.
preview_path = Path(__file__).resolve().parent / 'templates' / 'preview.html'
try:
    preview_html = preview_path.read_text(encoding='utf-8')
    valseriana_row = '<label class="check"><input type="checkbox" name="categories" value="valseriana"> valseriana</label>'
    valgandino_row = '<label class="check"><input type="checkbox" name="categories" value="val-gandino"> val-gandino</label>'
    if valgandino_row not in preview_html and valseriana_row in preview_html:
        preview_html = preview_html.replace(valseriana_row, valseriana_row + '\n        ' + valgandino_row)
        preview_path.write_text(preview_html, encoding='utf-8')
except Exception as exc:
    cp.base.log_exception('Errore aggiunta categoria Val Gandino alla preview', exc)

# Versione applicazione. Viene anche esposta direttamente al contesto Flask
# per evitare che il template mostri una versione ereditata precedente.
RUNTIME_APP_VERSION = '2.0.4'
cp.APP_VERSION = RUNTIME_APP_VERSION

@cp.app.context_processor
def inject_runtime_app_version():
    return {'app_version': RUNTIME_APP_VERSION}

cp.CHANGELOG.insert(0, {
    'version': '2.0.4',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la visualizzazione della versione: il numero release viene ora passato direttamente dal runtime al template Flask.',
    ],
})
cp.CHANGELOG.insert(1, {
    'version': '2.0.3',
    'date': '7 settembre 2026',
    'changes': [
        'Aggiunta la categoria WordPress Val Gandino (slug: val-gandino) tra le categorie Bergamo.',
    ],
})
cp.CHANGELOG.insert(2, {
    'version': '2.0.2',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la categoria WordPress Valle Seriana: Postie ora riceve [Valle Seriana] invece di [Val Seriana].',
        'Risolto il problema per cui [Val Seriana] rimaneva nel titolo pubblicato.',
    ],
})

app = cp.app

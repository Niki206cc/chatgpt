import confirmation_patch as cp

# Correzioni/integrazioni categorie Postie/WordPress.
cp.CATEGORY_NAMES['valseriana'] = 'Valle Seriana'
cp.CATEGORY_NAMES['val-gandino'] = 'Val Gandino'

# Versione applicazione
cp.APP_VERSION = '2.0.3'
cp.CHANGELOG.insert(0, {
    'version': '2.0.3',
    'date': '7 settembre 2026',
    'changes': [
        'Aggiunta la categoria WordPress Val Gandino (slug: val-gandino).',
    ],
})
cp.CHANGELOG.insert(1, {
    'version': '2.0.2',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la categoria WordPress Valle Seriana: Postie ora riceve [Valle Seriana] invece di [Val Seriana].',
        'Risolto il problema per cui [Val Seriana] rimaneva nel titolo pubblicato.',
    ],
})

app = cp.app

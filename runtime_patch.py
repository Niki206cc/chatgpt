import confirmation_patch as cp

# Correzione specifica Postie/WordPress:
# lo slug è "valseriana", ma il nome reale della categoria WordPress è "Valle Seriana".
cp.CATEGORY_NAMES['valseriana'] = 'Valle Seriana'

# Versione applicazione
cp.APP_VERSION = '2.0.2'
cp.CHANGELOG.insert(0, {
    'version': '2.0.2',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la categoria WordPress Valle Seriana: Postie ora riceve [Valle Seriana] invece di [Val Seriana].',
        'Risolto il problema per cui [Val Seriana] rimaneva nel titolo pubblicato.',
    ],
})

app = cp.app

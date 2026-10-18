# Ordinary D&D source catalog

loot.csv is the exact user-provided UTF-8 semicolon master, published with the
owner's explicit permission for this repository and CI. SHA-256:
7bffd8a9b8314651318c961838c9b6b872f5d02b6cd896e968f9226c7bcdfe20.
4450 records / 5188747 bytes. .gitattributes disables newline conversion and recognizes original CRLF endings
for whitespace checks; other trailing whitespace checks remain enabled.

loot_code_map.csv is the persistent reference index for a NEW empty shop.
For an existing database use CLI index against historical mappings;
never overwrite its index with this reference or regenerate on reorder.
shop_offers.example.csv has only a header: no default buys.
Live sources, index, offers, snapshot and lock stay outside Git checkout/watcher.

See ../docs/V1.4.4.md for staging, exact credit, tests and recovery.
No Telegram token, inventory dump or server database belongs here.

#!/bin/bash
# Pinned, portable constant-time AES for the DIPPER legacy CBC/ECB API.
set -euo pipefail
[[ "${MACHINE_CONFIG:-}" == DIPPER ]] || exit 0
[[ -f libkern/crypto/corecrypto_aes.c ]] || exit 1
bearssl_sha=7bea48e5e850ab4cafbe68d3765cdaba13a86d6f
bearssl_tmp=$(mktemp -d)
trap 'rm -rf "$bearssl_tmp"' EXIT
# autocrlf=false: a Windows global config would rewrite the staged sources to
# CRLF and every later patch that touches them would fail to apply.
git -c core.autocrlf=false init -q "$bearssl_tmp"
# persist it: -c only covers the single command, the fetch/checkout below need it too
git -C "$bearssl_tmp" config core.autocrlf false
git -C "$bearssl_tmp" fetch -q --depth 1 \
    https://www.bearssl.org/git/BearSSL "$bearssl_sha"
git -C "$bearssl_tmp" checkout -q FETCH_HEAD
[[ "$(git -C "$bearssl_tmp" rev-parse HEAD)" == "$bearssl_sha" ]]
python3 - "$bearssl_tmp" <<'PY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
target = Path('libkern/crypto/dipper_bearssl')
if target.exists():
    raise SystemExit(f'refusing to overwrite existing AES source: {target}')
shutil.copytree(source / 'inc', target / 'inc')
(target / 'src/symcipher').mkdir(parents=True)
for file in ('inner.h', 'config.h'):
    shutil.copy2(source / 'src' / file, target / 'src' / file)
# XNU exports machine/limits.h instead of the userspace limits.h.
# open(..., newline='') keeps LF on every Python >=3.7 (Path.write_text's
# newline= keyword needs 3.10+, which the macOS 14 runner's python3 3.9 lacks).
header = target / 'src/inner.h'
text = header.read_text()
assert text.count('#include <limits.h>') == 1
open(header, 'w', newline='').write(text.replace('#include <limits.h>',
    '#if defined(KERNEL)\n#include <machine/limits.h>\n'
    '#else\n#include <limits.h>\n#endif'))
for name in ('aes_ct', 'aes_ct_enc', 'aes_ct_dec', 'aes_ct_cbcenc', 'aes_ct_cbcdec'):
    shutil.copy2(source / 'src/symcipher' / (name + '.c'),
                 target / 'src/symcipher' / (name + '.c'))
# MD5, SHA-1 and SHA-384/512 for the legacy hash APIs, with their codecs.
for name in ('hash/md5.c', 'codec/dec32le.c', 'codec/enc32le.c',
             'hash/sha1.c', 'codec/dec32be.c', 'codec/enc32be.c',
             'hash/sha2big.c', 'codec/dec64be.c', 'codec/enc64be.c'):
    (target / 'src' / name).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'src' / name, target / 'src' / name)
shutil.copy2(source / 'LICENSE.txt', target / 'LICENSE.txt')
PY
echo "Staged BearSSL portable AES, MD5, SHA-1 and SHA-384/512 ($bearssl_sha) for ${MACHINE_CONFIG}"

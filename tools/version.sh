#!/bin/sh

# Sorry about the Debian nonsense in here but the build system doesn't
# patch the file early enough and overriding it is a PITA
if test -s debian/changelog && which dpkg-parsechangelog >/dev/null ; then
    dpkg-parsechangelog -SVersion | sed -e 's/.*://' -e 's/-.*//'
    exit
fi

test -d .git || exit
# git describe --tags
git log --format=format:%D | perl -ne 'next unless s#.*tag: ##; s#,.*##; next if m#/#; print; exit;'

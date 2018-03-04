# kakoune-cquery

kakoune-cquery is a [kakoune](kakoune.org) extension for [cquery](https://github.com/jacobdufault/cquery), a low-latency language server supporting multi-million line C++ code-bases, powered by libclang.

This repo is also a fork of [danr/libkak](https://github.com/danr/libkak), a python library for interacting with kakoune and lsp. Therefore it will also let you use other lsp servers for other languages. We will try to get the required changes upstreamed, or at least move our libkak into its own repo. For now, this is the easiest way to experiment with it though.

These are the lsp features that kakoune-cquery currently supports

* diagnostics
* find definition/references
* signature help (not supported by cquery)
* completion
* hover messages
* renaming

Additionally, it supports these cquery specific features:

* semantic highlighting

## Quickstart
Currently, you need to start cquery using a script like this one:
```sh
#!/bin/bash
cquery --init='{
  "cacheDirectory": "/tmp/cquery-cache-dir/",
  "cacheFormat": "msgpack",
  "completion": {
    "detailedLabel": true
  },
  "xref": {
    "container": true
  }
}'   
```

Then, in your kakrc
```kak
# Commands for language servers
decl str lsp_servers %{
    cpp:path/to/cquery-start
    # You can add any other language servers for other filetypes here
    python:pyls 
}

# Example keybindings
map -docstring %{Goto definition}     global user . ':lsp-goto-definition<ret>'
map -docstring %{Select references}   global user ? ':lsp-references<ret>'
map -docstring %{Rename}              global user r ':lsp-rename<ret>'
map -docstring %{Hover help}          global user h ':lsp-hover docsclient<ret>'
map -docstring %{Next diagnostic}     global user j ':lsp-diagnostics next cursor<ret>'
map -docstring %{Previous diagnostic} global user k ':lsp-diagnostics prev cursor<ret>'

# Manual completion and signature help when needed
map global insert <a-c> '<a-;>:eval -draft %(exec b; lsp-complete)<ret>'
map global insert <a-h> '<a-;>:lsp-signature-help<ret>'

# Hover and diagnostics on idle
hook -group lsp global NormalIdle .* %{
    lsp-diagnostics cursor
    lsp-hover cursor
}

# Aggressive diagnostics
hook -group lsp global InsertEnd .* lsp-sync

```

Then attach `cquery.py` to a running kak process. Say it has PID 4032 (you see
this in the lower right corner), then issue:

    python cquery.py 4032

If you want to start this from Kakoune add something like this to your kakrc:

```kak
def cquery-start %{
    %sh{
        ( python path/to/cquery-kakoune/cquery.py $kak_session
        ) > /dev/null 2>&1 < /dev/null &
    }
}
```

#!/bin/sh

#  compile_payload2.sh
#  jailbreak
#
#  Created by Phillip Tennen on 10/07/2023.
#  
as -arch armv7 entry.s -o build/entry.o
ld build/entry.o -U _main -U start -static -o build/shellcode_in_mediakit_packed
python3 ../dump_shellcode.py build/shellcode_in_mediakit_packed build/shellcode_in_mediakit_shellcode

.text

.extern _c_entry_point

.pool
.set printf, 0x21ef0

.extern _c_entry_point

.code 16
_start: .global _start
    push {lr}

    @ Preserve registers
    push {r0-r11}

    push {r6}
    push {r7}
    push {r8}

    push {r3}
    push {r4}
    push {r5}

    push {r0}
    push {r1}
    push {r2}

    adr r0, intro
    mov r1, lr
    sub r1, r1, #5
    ldr r11, =printf
    blx r11

    pop {r3}
    pop {r2}
    pop {r1}

    adr r0, msg1
    blx r11

    pop {r3}
    pop {r2}
    pop {r1}

    adr r0, msg1
    blx r11

    pop {r3}
    pop {r2}
    pop {r1}

    adr r0, msg1
    blx r11

    @ Restore register state before calling C functions
    pop {r0-r11}
    push {r0-r11}

    bl _c_entry_point

    @ Restore registers
    pop {r0-r11}

    pop {pc}

.align 2
intro:
.asciz "\n*** ASR Reg-dump @ 0x%08x\n\0"

msg1:
.asciz "0x%08x\n0x%08x\n0x%08x\n\0"
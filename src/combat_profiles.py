"""Closed EUR combat targets shared by wire validation and native guards.

This catalogue matches local EUR type, vtable and HP observations. A structural
match does not establish identical boss phases, virtual damage multipliers or
loot behaviour. The relay still checks the exact native consequence and stops
on divergence. Extending it requires native pipeline/response tests.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class SwordTarget:
    vtable:int
    max_hp:int


SWORD_TARGETS=MappingProxyType({
    18:SwordTarget(0x6DB088,24),
    19:SwordTarget(0x6D3244,8),
    20:SwordTarget(0x6D76A4,8),
    499:SwordTarget(0x6DB5F8,16),
    500:SwordTarget(0x6ED930,12),
    501:SwordTarget(0x6ED930,12),
    502:SwordTarget(0x6ED930,40),
    503:SwordTarget(0x6DDE90,96),
    504:SwordTarget(0x6ED930,36),
    505:SwordTarget(0x6D2B04,16),
    506:SwordTarget(0x6D267C,8),
    507:SwordTarget(0x6C8434,8),
    509:SwordTarget(0x6EE070,16),
    510:SwordTarget(0x6CEC90,12),
    512:SwordTarget(0x6D36DC,12),
    513:SwordTarget(0x6C611C,1),
    514:SwordTarget(0x6EDEA0,24),
    515:SwordTarget(0x6EDF88,8),
    516:SwordTarget(0x6EDCD0,8),
    517:SwordTarget(0x6C817C,4),
    518:SwordTarget(0x6C817C,8),
    519:SwordTarget(0x6EE158,8),
    520:SwordTarget(0x6EE158,8),
    521:SwordTarget(0x6CE378,28),
    522:SwordTarget(0x6CE378,84),
    523:SwordTarget(0x6CE460,16),
    524:SwordTarget(0x6CE460,32),
    525:SwordTarget(0x6CE460,64),
    526:SwordTarget(0x6CEBA4,8),
    527:SwordTarget(0x6CEBA4,12),
    528:SwordTarget(0x6CEBA4,8),
    529:SwordTarget(0x6CEBA4,8),
    530:SwordTarget(0x6CEBA4,16),
    531:SwordTarget(0x6CEAB8,12),
    532:SwordTarget(0x6CEAB8,16),
    533:SwordTarget(0x6CEBA4,12),
    534:SwordTarget(0x6CEBA4,16),
    535:SwordTarget(0x6CEAB8,12),
    536:SwordTarget(0x6CEBA4,16),
    537:SwordTarget(0x6CEBA4,20),
    538:SwordTarget(0x6CEBA4,24),
    539:SwordTarget(0x6CEAB8,24),
    540:SwordTarget(0x6C5C94,4),
    542:SwordTarget(0x6D2764,12),
    543:SwordTarget(0x6DB510,28),
    544:SwordTarget(0x6EDBE8,4),
    545:SwordTarget(0x6EDA18,8),
    546:SwordTarget(0x6D2F8C,8),
    547:SwordTarget(0x6D2EA4,24),
    548:SwordTarget(0x6D2A1C,120),
    549:SwordTarget(0x6EDDB8,1),
    550:SwordTarget(0x6EDDB8,12),
    551:SwordTarget(0x6C7FAC,12),
    552:SwordTarget(0x6D73EC,32),
    553:SwordTarget(0x6C5AC4,56),
    555:SwordTarget(0x6E35E0,1),
    556:SwordTarget(0x6C851C,74),
    563:SwordTarget(0x6EE240,8),
    564:SwordTarget(0x6CED78,40),
    565:SwordTarget(0x6CED78,56),
    566:SwordTarget(0x6CE8E8,8),
    567:SwordTarget(0x6CE8E8,16),
    568:SwordTarget(0x6D2934,40),
    569:SwordTarget(0x6E12D0,8),
    571:SwordTarget(0x6EBDF8,24),
    572:SwordTarget(0x6C59DC,24),
    573:SwordTarget(0x6DB428,24),
    574:SwordTarget(0x6D387C,160),
    575:SwordTarget(0x6DB7C8,20),
    576:SwordTarget(0x6D387C,160),
    577:SwordTarget(0x6DB7C8,20),
    578:SwordTarget(0x6C6204,8),
    579:SwordTarget(0x6EBD10,8),
    580:SwordTarget(0x6C580C,16),
    581:SwordTarget(0x6CE630,16),
    582:SwordTarget(0x6CE718,16),
    583:SwordTarget(0x6D74D4,80),
    585:SwordTarget(0x6EBC20,1),
    586:SwordTarget(0x6C5D7C,80),
    587:SwordTarget(0x6D2CD4,80),
    588:SwordTarget(0x6C8094,4),
    589:SwordTarget(0x6CE290,4),
    590:SwordTarget(0x6CE290,4),
    591:SwordTarget(0x6C87D4,4),
    592:SwordTarget(0x6C5E64,12),
    593:SwordTarget(0x6D332C,8),
    595:SwordTarget(0x6D2BEC,1),
    596:SwordTarget(0x6E4F5C,1),
    597:SwordTarget(0x6DE060,60),
    598:SwordTarget(0x6E8274,20),
    599:SwordTarget(0x6DE060,120),
    600:SwordTarget(0x6DAFA0,16),
    601:SwordTarget(0x6EB244,16),
    602:SwordTarget(0x6C8264,16),
    603:SwordTarget(0x6D3074,4),
    604:SwordTarget(0x6C8604,16),
    605:SwordTarget(0x6C58F4,8),
    607:SwordTarget(0x6C5F4C,4),
    608:SwordTarget(0x6C5F4C,8),
    609:SwordTarget(0x6DE31C,20),
    610:SwordTarget(0x6D2594,99),
    614:SwordTarget(0x6E11E8,192),
    616:SwordTarget(0x6DE5D4,8),
    617:SwordTarget(0x6DB8B0,8),
    618:SwordTarget(0x6C6034,120),
    619:SwordTarget(0x6D3414,24),
    620:SwordTarget(0x6CE548,16),
    621:SwordTarget(0x6C5BAC,2),
    622:SwordTarget(0x6DB340,2),
    624:SwordTarget(0x6CE800,16),
    625:SwordTarget(0x6C834C,16),
})


def sword_target(kind):
    return SWORD_TARGETS.get(kind) if type(kind) is int else None


@dataclass(frozen=True)
class AttackProfile:
    """One AttackDesc consumed natively in EUR captures, with its HitInfo words.

    desc is copied by 34C8CC into the record (+0x20..+0x38); byte 1 is the
    reaction type: candidate mask 1<<type, sensitivity table+0xE+2*type.
    category/kind are HitInfo+0x20 (low half) and +0x24, flags HitInfo+0x30
    (copied to record+0x38). The wire only ever carries an index here.
    """
    name:str
    desc:bytes
    category:int
    kind:int
    flags:int

    @property
    def reaction(self):return self.desc[1]


# Captured 19-22/10 (.local/research/native-hits-*.jsonl). The sword keeps the
# HitInfo words of the replay validated in game (+0x20/+0x24 zero).
ATTACK_PROFILES=(
    AttackProfile('sword',bytes.fromhex('090500000000000000000000000000000800ffff0000803f'),0,0,0x04000000),
    AttackProfile('sword_strong',bytes.fromhex('090500000000000000000000000000001000ffff0000803f'),0,0,0x24000000),
    AttackProfile('impact_06',bytes.fromhex('0c0600000000000000000000000000000400ffff0000803f'),0,0x20000,0),
    AttackProfile('impact_0f',bytes.fromhex('0c0f07010000000000000000000000000c00ffff0000803f'),0xE0,0x8000,0),
    AttackProfile('impact_00',bytes.fromhex('0c0000000000000000000000000000000400ffff0000803f'),0,0,0),
)
SWORD_PROFILE=0


def attack_profile(desc):
    """Index of a captured AttackDesc, or None for an unknown one."""
    return next((i for i,p in enumerate(ATTACK_PROFILES) if p.desc==desc),None)


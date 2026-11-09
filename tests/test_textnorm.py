from editais.textnorm import clean_lines, fix_glyphs


def test_replaces_standard_fi_ligature():
    assert fix_glyphs("eﬁciência") == "eficiência"


def test_replaces_misread_ti_glyph():
    # Real case from call 116: "desƟnados" -> "destinados"
    assert fix_glyphs("desƟnados") == "destinados"


def test_keeps_uppercase_context():
    assert fix_glyphs("OBJEƟVO") == "OBJETIVO"


def test_keeps_clean_text_unchanged():
    text = "A proposta deverá ser submetida até as 17h."
    assert fix_glyphs(text) == text


def test_removes_sei_footer_and_reads_its_metadata():
    # Synthetic footer in the SEI/GOVMG format; replace with a real one from call 116 when possible.
    pages = [{"page": 1, "text": (
        "16.21 São partes constituintes desta Chamada os seus Anexos.\n"
        "05/03/2026 14:32\n"
        "SEI/GOVMG - 123456 - Chamada\n"
        "https://www.sei.mg.gov.br/sei/controlador.php?acao=documento_imprimir_web\n"
        "3/10\n"
    )}]
    lines, info = clean_lines(pages)
    assert lines == [(1, "16.21 São partes constituintes desta Chamada os seus Anexos.")]
    assert info == {"datas": ["05/03/2026 14:32"], "numeros": ["123456"]}

from editais.evidence import check_evidence, index_lines

LINES = [
    (3, "4.1 As propostas deverao ser submetidas exclusivamente pelo sistema"),
    (3, "eletronico ate as 17h do dia 15 de marco de 2026, horario de Brasilia."),
    (4, "5. DO VALOR"),
    (4, "5.1 O valor global desta chamada e de R$ 2.000.000,00 (dois milhoes de reais)."),
    (9, "ANEXO I - MINUTA DO TERMO DE OUTORGA"),
    (9, "CLAUSULA SETIMA - Os direitos de propriedade intelectual serao compartilhados."),
]
DOC = index_lines(LINES, annex_start=4)


def test_quote_across_line_break_is_found_with_page():
    c = check_evidence("ate as 17h do dia 15 de marco de 2026", "17h do dia 15 de marco de 2026", DOC)
    assert (c.ok, c.page, c.region) == (True, 3, "corpo")


def test_extra_spaces_and_line_breaks_in_quote_are_tolerated():
    c = check_evidence("sistema\n  eletronico ate as 17h", "17h", DOC)
    assert c.ok


def test_misread_glyph_in_document_is_tolerated():
    doc = index_lines([(1, "Recursos desƟnados a projetos")])  # PDF glyph for "ti"
    assert check_evidence("Recursos destinados a projetos", None, doc).ok


def test_invented_quote_is_rejected():
    c = check_evidence("ate as 18h do dia 20 de marco de 2026", "18h do dia 20 de marco de 2026", DOC)
    assert not c.ok and "not found" in c.reason


def test_value_must_be_inside_quote():
    c = check_evidence("5.1 O valor global desta chamada", "R$ 2.000.000,00", DOC)
    assert not c.ok and "value" in c.reason


def test_overlong_quote_is_rejected():
    c = check_evidence(" ".join(line for _, line in LINES), None, DOC, max_chars=200)
    assert not c.ok and "longer" in c.reason


def test_quote_in_annex_gets_annex_region():
    c = check_evidence("Os direitos de propriedade intelectual serao compartilhados", None, DOC)
    assert (c.ok, c.page, c.region) == (True, 9, "anexo")


def test_document_without_annex_is_all_body():
    doc = index_lines(LINES, annex_start=None)
    assert check_evidence("Os direitos de propriedade intelectual", None, doc).region == "corpo"


def test_index_document_uses_annex_boundary_from_structure():
    from editais.evidence import index_document
    def line(t, bold=False):
        return {"t": t, "bold": bold, "size": 11.0}

    doc = {"pages": [
        {"page": 1, "lines": [line("1. OBJETIVO", True), line("Apoiar projetos de pesquisa.")]},
        {"page": 2, "lines": [line("ANEXO I - MODELO DE PROPOSTA", True), line("Preencher todos os campos.")]},
    ]}
    located = index_document(doc)
    assert check_evidence("Apoiar projetos de pesquisa.", None, located).region == "corpo"
    c = check_evidence("Preencher todos os campos.", None, located)
    assert (c.page, c.region) == (2, "anexo")

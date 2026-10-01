from urbex_crm.enrichment.guesser import candidates, infer_pattern, split_name


def test_split_spanish_two_surnames_uses_first_surname():
    assert split_name("Juan Carlos") == ("juan", "carlos")
    assert split_name("María García López") == ("maria", "garcia")
    assert split_name("José de la Fuente") == ("jose", "fuente")


def test_candidates_order_and_dedup():
    c = candidates("Ana Ruiz", "example.com")
    emails = [e for _, e in c]
    assert emails[0] == "ana.ruiz@example.com"
    assert "aruiz@example.com" in emails and len(emails) == len(set(emails))


def test_preferred_pattern_first():
    c = candidates("Ana Ruiz", "example.com", preferred_pattern="flast")
    assert c[0] == ("flast", "aruiz@example.com")


def test_infer_pattern():
    assert infer_pattern("l.gil@x.com", "Laura Gil") == "f.last"
    assert infer_pattern("laura@x.com", "Laura Gil") == "first"
    assert infer_pattern("weird@x.com", "Laura Gil") is None

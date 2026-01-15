"""
Utilitaires pour le parsing JSON robuste
"""
import json
import re


def clean_json_codefence(s):
    """Remove optional Markdown codefence like ```json ... ```"""
    s = s.strip()
    s = re.sub(r'^```[a-zA-Z]*\n?', '', s)
    s = re.sub(r'\n?```$', '', s)
    return s.strip()


def lire_json_beton(json_str):
    """
    Fonction robuste pour lire et décoder un JSON, même s'il y a des codefences ou du texte superflu autour,
    ou des retours à la ligne, ou des erreurs classiques de mise en forme.
    Retourne un objet Python (dict, list...).
    Si impossible, retourne {}.
    """
    try:
        # Extraction entre les éventuels codefences ```json ... ```
        match = re.search(r"```json(.*?)```", json_str, flags=re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            # Parfois le code fence n'est pas respecté, on cherche juste un code fence classique
            match2 = re.search(r"```(.*?)```", json_str, flags=re.DOTALL)
            if match2:
                text = match2.group(1).strip()
            else:
                text = json_str.strip()
        # Essai direct
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Essai après suppression de lignes parasites en début/fin
        lines = text.splitlines()
        # On cherche la première et dernière vraie ligne json
        start_idx, end_idx = 0, len(lines)
        # Cherche clé ouvrante et fermante
        for i, l in enumerate(lines):
            if "{" in l or "[" in l:
                start_idx = i
                break
        for i in range(len(lines)-1, -1, -1):
            if "}" in lines[i] or "]" in lines[i]:
                end_idx = i+1
                break
        subtext = "\n".join(lines[start_idx:end_idx])
        try:
            return json.loads(subtext)
        except Exception:
            pass
        # Tentative de correction des quotes simples vers doubles
        text2 = text.replace("'", '"')
        try:
            return json.loads(text2)
        except Exception:
            pass
        # Dernier recours: supprime tout avant le premier { et après le dernier }
        if "{" in text and "}" in text:
            t2 = text[text.find("{"):text.rfind("}")+1]
            try:
                return json.loads(t2)
            except Exception:
                pass
        # Échec final
        return {}
    except Exception:
        return {}

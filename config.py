#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration du scraper juridique et fiscal
"""

# Configuration générale
SCRAPER_CONFIG = {
    # Délai entre les requêtes (en secondes)
    'default_delay': 2.0,
    
    # Timeout des requêtes HTTP (en secondes)
    'default_timeout': 30,
    
    # Dossier de sortie par défaut
    'default_output_dir': 'scraped_content',
    
    # Nombre maximum de tentatives en cas d'échec
    'max_retries': 3,
    
    # Délai entre les tentatives (en secondes)
    'retry_delay': 5.0,
}

# Configuration des sites
SITE_CONFIGS = {
    'legifrance.gouv.fr': {
        'type': 'legislation',
        'delay': 3.0,  # Délai plus long pour Legifrance
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.texte', '.contenu', 'main'],
            'metadata': {
                'date': ['.date', 'time'],
                'numero': ['.numero', '.reference'],
                'nature': ['.nature', '.type']
            }
        }
    },
    
    'bofip.impots.gouv.fr': {
        'type': 'fiscal',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', '#contenu', 'main'],
            'metadata': {
                'date_publication': ['.date', 'time'],
                'numero_bofip': ['.numero', '.reference']
            }
        }
    },
    
    'conseil-etat.fr': {
        'type': 'jurisprudence',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', 'article', 'main'],
            'metadata': {
                'date_decision': ['.date', 'time'],
                'numero_decision': ['.numero', '.reference'],
                'formation': ['.formation', '.chambre']
            }
        }
    },
    
    'courdecassation.fr': {
        'type': 'jurisprudence',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', '.texte', 'main'],
            'metadata': {
                'date_decision': ['.date', 'time'],
                'numero_decision': ['.numero', '.reference']
            }
        }
    },
    
    'conseil-constitutionnel.fr': {
        'type': 'jurisprudence',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', '.decision', 'main'],
            'metadata': {
                'date_decision': ['.date', 'time'],
                'numero_decision': ['.numero', '.reference'],
                'formation': ['.formation', '.chambre']
            }
        }
    },
    
    'assemblee-nationale.fr': {
        'type': 'parlementaire',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', '.texte', 'main'],
            'metadata': {
                'date_seance': ['.date', 'time'],
                'numero_seance': ['.numero', '.reference'],
                'type_document': ['.type', '.categorie']
            }
        }
    },
    
    'senat.fr': {
        'type': 'parlementaire',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', '.texte', 'main'],
            'metadata': {
                'date_seance': ['.date', 'time'],
                'numero_seance': ['.numero', '.reference'],
                'type_document': ['.type', '.categorie']
            }
        }
    },
    
    'fiscalonline.com': {
        'type': 'fiscal',
        'delay': 2.0,
        'selectors': {
            'title': ['h1', 'title'],
            'content': ['.contenu', 'main', 'article'],
            'metadata': {
                'date_publication': ['.date', 'time'],
                'categorie': ['.categorie', '.type']
            }
        }
    }
}

# Configuration des headers HTTP
HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Configuration des formats de sortie
OUTPUT_FORMATS = {
    'text': {
        'enabled': True,
        'extension': '.txt',
        'include_metadata': True,
        'include_raw_html': False
    },
    'html': {
        'enabled': True,
        'extension': '.html',
        'include_metadata': False,
        'include_raw_html': True
    },
    'json': {
        'enabled': True,
        'extension': '.json',
        'include_metadata': True,
        'include_raw_html': False
    },
    'markdown': {
        'enabled': False,
        'extension': '.md',
        'include_metadata': True,
        'include_raw_html': False
    }
}

# Configuration du logging
LOGGING_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(levelname)s - %(message)s',
    'file': 'scraper.log',
    'max_size': 10 * 1024 * 1024,  # 10 MB
    'backup_count': 5
}

# Configuration des mots-clés juridiques
LEGAL_KEYWORDS = {
    'legislation': ['loi', 'décret', 'arrêté', 'code', 'article', 'chapitre', 'section'],
    'fiscal': ['impôt', 'taxe', 'fiscal', 'déduction', 'crédit', 'assiette', 'taux'],
    'jurisprudence': ['décision', 'arrêt', 'jugement', 'cassation', 'annulation', 'rejet'],
    'parlementaire': ['séance', 'débat', 'amendement', 'proposition', 'question', 'audition']
}

# Configuration des limites
LIMITS = {
    'max_content_length': 10 * 1024 * 1024,  # 10 MB
    'max_urls_per_batch': 100,
    'max_concurrent_requests': 1,  # Séquentiel pour respecter les sites
    'max_retries_per_url': 3
}

# Configuration des délais par défaut selon le type de site
DEFAULT_DELAYS = {
    'legislation': 3.0,
    'fiscal': 2.0,
    'jurisprudence': 2.0,
    'parlementaire': 2.0,
    'unknown': 1.0
}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper spécialisé pour les sites juridiques et fiscaux français
Sites supportés:
- legifrance.gouv.fr
- bofip.impots.gouv.fr
- conseil-etat.fr
- courdecassation.fr
- conseil-constitutionnel.fr
- assemblee-nationale.fr
- senat.fr
- fiscalonline.com
- europa.eu (CJUE)
"""

import requests
import trafilatura
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time
import re
from typing import Dict, Optional, List, Tuple
import logging
from dataclasses import dataclass
from pathlib import Path
import json

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ScrapedContent:
    """Structure pour le contenu scrapé"""
    url: str
    title: str
    content: str
    metadata: Dict
    site_type: str
    timestamp: str
    raw_html: str

class LegalScraper:
    """Scraper principal pour les sites juridiques et fiscaux français"""
    
    def __init__(self, delay: float = 0.3, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update( {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    })
        self.delay = delay
        self.timeout = timeout
        
        # Mapping des sites vers leurs types
        self.site_mapping = {
            'legifrance.gouv.fr': 'legislation',
            'bofip.impots.gouv.fr': 'fiscal',
            'conseil-etat.fr': 'jurisprudence',
            'courdecassation.fr': 'jurisprudence',
            'conseil-constitutionnel.fr': 'jurisprudence',
            'assemblee-nationale.fr': 'parlementaire',
            'senat.fr': 'parlementaire',
            'fiscalonline.com': 'fiscal',
            'europa.eu': 'jurisprudence_eu'
        }
    
    def get_site_type(self, url: str) -> str:
        """Détermine le type de site à partir de l'URL"""
        domain = urlparse(url).netloc.lower()
        for site, site_type in self.site_mapping.items():
            if site in domain:
                return site_type
        return 'unknown'
    
    def scrape_url(self, url: str) -> Optional[ScrapedContent]:
        """Méthode principale de scraping"""
        try:
            logger.info(f"Scraping de l'URL: {url}")
            
            # Délai pour respecter les bonnes pratiques
            time.sleep(self.delay)
            
            # Récupération de la page
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            
            # Détermination du type de site
            site_type = self.get_site_type(url)
            
            # Scraping spécialisé selon le site
            if site_type == 'legislation':
                return self._scrape_legifrance(url, response)
            elif site_type == 'fiscal':
                return self._scrape_fiscal_site(url, response)
            elif site_type == 'jurisprudence':
                return self._scrape_jurisprudence_site(url, response)
            elif site_type == 'parlementaire':
                return self._scrape_parliamentary_site(url, response)
            elif site_type == 'jurisprudence_eu':
                return self._scrape_curia(url, response)
            else:
                return self._scrape_generic(url, response)
                
        except Exception as e:
            logger.error(f"Erreur lors du scraping de {url}: {str(e)}")
            return None
    
    def _scrape_legifrance(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping spécialisé pour Legifrance"""
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraction du titre
        title = ""
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Extraction du contenu principal
        content = ""
        main_content = soup.find('div', class_='texte') or soup.find('div', class_='contenu')
        if main_content:
            content = main_content.get_text(strip=True)
        else:
            # Fallback avec trafilatura
            content = trafilatura.extract(response.text, include_formatting=True) or ""
        
        # Métadonnées spécifiques à Legifrance
        metadata = self._extract_legifrance_metadata(soup)
        
        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='legislation',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )
    
    def _scrape_fiscal_site(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping spécialisé pour les sites fiscaux (BOFiP, FiscalOnline)"""
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraction du titre
        title = ""
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Extraction du contenu
        content = ""
        if 'bofip.impots.gouv.fr' in url:
            # Spécifique au BOFiP - sélecteurs basés sur la structure réelle
            main_content = None
            
            # Essayer d'abord l'article principal du BOFiP
            main_content = soup.find('article', class_='bofip-content')
            
            # Fallback vers d'autres sélecteurs BOFiP
            if not main_content:
                main_content = soup.find('div', class_='bofip-content')
            if not main_content:
                main_content = soup.find('div', class_='field--name-body')
            if not main_content:
                main_content = soup.find('div', class_='contenu')
            if not main_content:
                main_content = soup.find('div', id='contenu')
            
            if main_content:
                # Extraction structurée du contenu BOFiP
                content_parts = []
                
                # Titre principal
                if title:
                    content_parts.append(f"TITRE: {title}")
                    content_parts.append("")
                
                # Paragraphes numérotés
                paragraphes = main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                for para in paragraphes:
                    if para.get('class') and 'numero-de-paragraphe-western' in para.get('class'):
                        # Numéro de paragraphe
                        content_parts.append(f"\n{para.get_text(strip=True)}")
                    elif para.get('class') and 'paragraphe-western' in para.get('class'):
                        # Contenu du paragraphe
                        content_parts.append(para.get_text(strip=True))
                    elif para.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                        # Titres de section
                        content_parts.append(f"\n{para.get_text(strip=True).upper()}")
                    else:
                        # Autres paragraphes
                        text = para.get_text(strip=True)
                        if text:
                            content_parts.append(text)
                
                content = "\n".join(content_parts)
            else:
                # Fallback avec trafilatura si aucun sélecteur spécifique ne fonctionne
                content = trafilatura.extract(response.text, include_formatting=True) or ""
        else:
            # Fallback avec trafilatura pour les autres sites fiscaux
            content = trafilatura.extract(response.text, include_formatting=True) or ""
        
        metadata = self._extract_fiscal_metadata(soup, url)
        
        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='fiscal',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )
    
    def _scrape_jurisprudence_site(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping spécialisé pour les sites de jurisprudence"""
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraction du titre
        title = ""
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Extraction du contenu
        content = ""
        if 'conseil-etat.fr' in url:
            # Conseil d'État
            main_content = soup.find('div', class_='contenu') or soup.find('article')
            if main_content:
                content = main_content.get_text(strip=True)
                
        elif 'courdecassation.fr' in url:
            # Cour de Cassation - gestion spéciale pour les sites JavaScript
            content = self._extract_courdecassation_content(soup, response.text)
            
        elif 'conseil-constitutionnel.fr' in url:
            # Conseil Constitutionnel
            main_content = soup.find('div', class_='decision') or soup.find('div', class_='contenu')
            if main_content:
                content = main_content.get_text(strip=True)
        else:
            main_content = None
        
        # Fallback si aucun contenu n'a été extrait
        if not content:
            content = trafilatura.extract(response.text, include_formatting=True) or ""
        
        metadata = self._extract_jurisprudence_metadata(soup, url)
        
        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='jurisprudence',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )
    
    def _extract_courdecassation_content(self, soup: BeautifulSoup, html_text: str) -> str:
        """Extraction spécialisée du contenu de la Cour de Cassation"""
        content = ""
        
        # Vérifier si le contenu est chargé par JavaScript
        if "Le JavaScript n'est pas activé" in html_text or "JavaScript n'est pas activé" in html_text:
            # Le site nécessite JavaScript, essayer des alternatives
            logger.warning("Site Cour de Cassation nécessite JavaScript, tentative d'extraction alternative")
            
            # Essayer de trouver des éléments cachés ou des données JSON
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and ('data' in script.string.lower() or 'decision' in script.string.lower()):
                    # Essayer d'extraire des données JSON des scripts
                    try:
                        json_matches = re.findall(r'\{[^{}]*"decision"[^{}]*\}', script.string)
                        if json_matches:
                            logger.info("Données JSON trouvées dans les scripts")
                            # Traiter les données JSON trouvées
                            for match in json_matches:
                                content += match + "\n"
                    except Exception as e:
                        logger.debug(f"Erreur lors de l'extraction JSON: {e}")
            
            # Essayer de trouver des éléments avec des attributs data
            data_elements = soup.find_all(attrs={"data-": True})
            for elem in data_elements:
                for attr, value in elem.attrs.items():
                    if attr.startswith('data-') and value:
                        content += f"{attr}: {value}\n"
            
            # Essayer de trouver des éléments cachés
            hidden_elements = soup.find_all(style=lambda x: x and 'display: none' in x if x else False)
            for elem in hidden_elements:
                text = elem.get_text(strip=True)
                if text and len(text) > 10:  # Éviter les textes trop courts
                    content += text + "\n"
        
        # Essayer les sélecteurs traditionnels
        if not content:
            # Recherche de contenu principal
            main_selectors = [
                'div[class*="contenu"]',
                'div[class*="texte"]',
                'div[class*="decision"]',
                'div[class*="content"]',
                'main',
                'article',
                'div[role="main"]'
            ]
            
            for selector in main_selectors:
                try:
                    elements = soup.select(selector)
                    for elem in elements:
                        text = elem.get_text(strip=True)
                        if text and len(text) > 50:  # Éviter les textes trop courts
                            content += text + "\n"
                            break
                    if content:
                        break
                except Exception as e:
                    logger.debug(f"Erreur avec le sélecteur {selector}: {e}")
        
        # Si toujours pas de contenu, essayer trafilatura
        if not content:
            try:
                content = trafilatura.extract(html_text, include_formatting=True) or ""
                if content and len(content) > 100:
                    logger.info("Contenu extrait avec trafilatura")
                else:
                    logger.warning("Trafilatura n'a pas extrait de contenu significatif")
            except Exception as e:
                logger.error(f"Erreur avec trafilatura: {e}")
        
        # Nettoyer le contenu
        if content:
            # Supprimer les messages d'erreur JavaScript
            content = re.sub(r'Le JavaScript n\'est pas activé.*?Comment activer le JavaScript.*', '', content, flags=re.DOTALL)
            content = re.sub(r'JavaScript n\'est pas activé.*?Comment activer le JavaScript.*', '', content, flags=re.DOTALL)
            content = re.sub(r'Pour vous permettre de naviguer.*?activé\.', '', content, flags=re.DOTALL)
            
            # Nettoyer les espaces multiples
            content = re.sub(r'\n\s*\n', '\n\n', content)
            content = content.strip()
        
        return content
    
    def _scrape_parliamentary_site(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping spécialisé pour les sites parlementaires"""
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraction du titre
        title = ""
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Extraction du contenu
        content = ""
        if 'assemblee-nationale.fr' in url:
            main_content = soup.find('div', class_='contenu') or soup.find('div', class_='texte')
        elif 'senat.fr' in url:
            main_content = soup.find('div', class_='contenu') or soup.find('div', class_='texte')
        else:
            main_content = None
        
        if main_content:
            content = main_content.get_text(strip=True)
        else:
            content = trafilatura.extract(response.text, include_formatting=True) or ""
        
        metadata = self._extract_parliamentary_metadata(soup, url)
        
        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='parlementaire',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )

    def _scrape_curia(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping spécialisé pour le site de la CJUE (curia.europa.eu)"""
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extraction du titre
        title = ""
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)

        # Extraction du contenu
        content = ""

        # Essayer les sélecteurs spécifiques à Curia
        main_content = (
            soup.find('div', class_='content') or
            soup.find('div', id='document_content') or
            soup.find('div', class_='doc-content') or
            soup.find('article') or
            soup.find('main')
        )

        if main_content:
            content = main_content.get_text(strip=True)
        else:
            # Fallback avec trafilatura
            content = trafilatura.extract(response.text, include_formatting=True) or ""

        # Extraction des métadonnées CJUE
        metadata = self._extract_curia_metadata(soup, url)

        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='jurisprudence_eu',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )

    def _extract_curia_metadata(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extraction des métadonnées spécifiques à la CJUE"""
        metadata = {
            'source': 'CJUE',
            'language': 'fr'
        }

        # Numéro d'affaire (pattern C-xxx/xx)
        case_number = re.search(r'C-\d+/\d+', url) or re.search(r'C-\d+/\d+', soup.get_text())
        if case_number:
            metadata['case_number'] = case_number.group()

        # Date de décision
        date_elem = soup.find('span', class_='date') or soup.find('time')
        if date_elem:
            metadata['date_decision'] = date_elem.get_text(strip=True)

        # Type de document (arrêt, conclusions, etc.)
        doc_type_elem = soup.find('span', class_='doc-type') or soup.find('div', class_='document-type')
        if doc_type_elem:
            metadata['type_document'] = doc_type_elem.get_text(strip=True)

        return metadata

    def _scrape_generic(self, url: str, response: requests.Response) -> ScrapedContent:
        """Scraping générique avec trafilatura"""
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraction du titre
        title = ""
        title_elem = soup.find('title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        
        # Extraction du contenu avec trafilatura
        content = trafilatura.extract(response.text, include_formatting=True) or ""
        
        metadata = {
            'language': 'fr',
            'extraction_method': 'trafilatura',
            'charset': response.encoding
        }
        
        return ScrapedContent(
            url=url,
            title=title,
            content=content,
            metadata=metadata,
            site_type='unknown',
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            raw_html=response.text
        )
    
    def _extract_legifrance_metadata(self, soup: BeautifulSoup) -> Dict:
        """Extraction des métadonnées spécifiques à Legifrance"""
        metadata = {}
        
        # Recherche des éléments de métadonnées
        date_elem = soup.find('span', class_='date') or soup.find('time')
        if date_elem:
            metadata['date'] = date_elem.get_text(strip=True)
        
        # Numéro de texte
        numero_elem = soup.find('span', class_='numero') or soup.find('div', class_='numero')
        if numero_elem:
            metadata['numero'] = numero_elem.get_text(strip=True)
        
        # Nature du texte
        nature_elem = soup.find('span', class_='nature') or soup.find('div', class_='nature')
        if nature_elem:
            metadata['nature'] = nature_elem.get_text(strip=True)
        
        return metadata
    
    def _extract_fiscal_metadata(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extraction des métadonnées fiscales"""
        metadata = {}
        
        # Date de publication
        date_elem = soup.find('span', class_='date') or soup.find('time')
        if date_elem:
            metadata['date_publication'] = date_elem.get_text(strip=True)
        
        if 'bofip.impots.gouv.fr' in url:
            # Métadonnées spécifiques au BOFiP
            
            # Identifiant BOFiP (data-legalid)
            article_elem = soup.find('article', class_='bofip-content')
            if article_elem and article_elem.get('data-legalid'):
                metadata['identifiant_bofip'] = article_elem.get('data-legalid')
            
            # PGP ID
            if article_elem and article_elem.get('data-pgpid'):
                metadata['pgp_id'] = article_elem.get('data-pgpid')
            
            # Numéro de BOFiP
            numero_elem = soup.find('span', class_='numero') or soup.find('div', class_='numero')
            if numero_elem:
                metadata['numero_bofip'] = numero_elem.get_text(strip=True)
            
            # Titre du document
            titre_elem = soup.find('h1', class_='titre-du-document-western')
            if titre_elem:
                metadata['titre_document'] = titre_elem.get_text(strip=True)
            
            # Structure du document (paragraphes)
            paragraphes = soup.find_all('p', class_='numero-de-paragraphe-western')
            if paragraphes:
                metadata['nombre_paragraphes'] = len(paragraphes)
                metadata['paragraphes'] = [p.get_text(strip=True) for p in paragraphes[:5]]  # Premiers 5
            
            # Sections principales
            sections = soup.find_all(['h1', 'h2', 'h3'], recursive=False)
            if sections:
                metadata['sections'] = [s.get_text(strip=True) for s in sections[:10]]  # Premières 10 sections
        
        return metadata
    
    def _extract_jurisprudence_metadata(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extraction des métadonnées de jurisprudence"""
        metadata = {}
        
        # Date de décision
        date_elem = soup.find('span', class_='date') or soup.find('time')
        if date_elem:
            metadata['date_decision'] = date_elem.get_text(strip=True)
        
        # Numéro de décision
        numero_elem = soup.find('span', class_='numero') or soup.find('div', class_='numero')
        if numero_elem:
            metadata['numero_decision'] = numero_elem.get_text(strip=True)
        
        # Formation
        formation_elem = soup.find('span', class_='formation') or soup.find('div', class_='formation')
        if formation_elem:
            metadata['formation'] = formation_elem.get_text(strip=True)
        
        # Métadonnées spécifiques à la Cour de Cassation
        if 'courdecassation.fr' in url:
            # Essayer d'extraire des métadonnées des scripts
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Recherche de patterns JSON dans les scripts
                    import re
                    
                    # Recherche d'identifiants de décision
                    decision_matches = re.findall(r'"decision_id"\s*:\s*"([^"]+)"', script.string)
                    if decision_matches:
                        metadata['decision_id'] = decision_matches[0]
                    
                    # Recherche de dates
                    date_matches = re.findall(r'"date"\s*:\s*"([^"]+)"', script.string)
                    if date_matches:
                        metadata['date_script'] = date_matches[0]
                    
                    # Recherche de numéros
                    numero_matches = re.findall(r'"numero"\s*:\s*"([^"]+)"', script.string)
                    if numero_matches:
                        metadata['numero_script'] = numero_matches[0]
            
            # Recherche d'attributs data sur les éléments
            data_elements = soup.find_all(attrs={"data-": True})
            for elem in data_elements:
                for attr, value in elem.attrs.items():
                    if attr.startswith('data-') and value:
                        metadata[f"data_{attr[5:]}"] = value
            
            # Recherche d'éléments avec des classes spécifiques
            specific_elements = soup.find_all(class_=lambda x: x and any(keyword in x.lower() for keyword in ['decision', 'date', 'numero', 'formation']))
            for elem in specific_elements:
                class_name = ' '.join(elem.get('class', []))
                text = elem.get_text(strip=True)
                if text and len(text) < 100:  # Éviter les textes trop longs
                    metadata[f"class_{class_name.replace(' ', '_')}"] = text
        
        return metadata
    
    def _extract_parliamentary_metadata(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extraction des métadonnées parlementaires"""
        metadata = {}
        
        # Date de séance
        date_elem = soup.find('span', class_='date') or soup.find('time')
        if date_elem:
            metadata['date_seance'] = date_elem.get_text(strip=True)
        
        # Numéro de séance
        numero_elem = soup.find('span', class_='numero') or soup.find('div', class_='numero')
        if numero_elem:
            metadata['numero_seance'] = numero_elem.get_text(strip=True)
        
        # Type de document
        type_elem = soup.find('span', class_='type') or soup.find('div', class_='type')
        if type_elem:
            metadata['type_document'] = type_elem.get_text(strip=True)
        
        return metadata
    
    def save_content(self, content: ScrapedContent, output_dir: str = "scraped_content") -> str:
        """Sauvegarde du contenu scrapé"""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        # Nom de fichier basé sur l'URL
        safe_filename = re.sub(r'[^\w\-_.]', '_', content.url)
        safe_filename = safe_filename[:100]  # Limiter la longueur
        
        # Sauvegarde du contenu texte
        text_file = output_path / f"{safe_filename}.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(f"URL: {content.url}\n")
            f.write(f"Titre: {content.title}\n")
            f.write(f"Type de site: {content.site_type}\n")
            f.write(f"Timestamp: {content.timestamp}\n")
            f.write(f"Métadonnées: {json.dumps(content.metadata, indent=2, ensure_ascii=False)}\n")
            f.write("\n" + "="*80 + "\n\n")
            f.write(content.content)
        
        # Sauvegarde du HTML brut
        html_file = output_path / f"{safe_filename}.html"
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(content.raw_html)
        
        # Sauvegarde des métadonnées en JSON
        json_file = output_path / f"{safe_filename}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump({
                'url': content.url,
                'title': content.title,
                'content': content.content,
                'metadata': content.metadata,
                'site_type': content.site_type,
                'timestamp': content.timestamp
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Contenu sauvegardé dans {output_path}")
        return str(output_path)
    
    def batch_scrape(self, urls: List[str], output_dir: str = "scraped_content") -> List[ScrapedContent]:
        """Scraping en lot de plusieurs URLs"""
        results = []
        
        for i, url in enumerate(urls, 1):
            logger.info(f"Scraping {i}/{len(urls)}: {url}")
            
            try:
                content = self.scrape_url(url)
                if content:
                    results.append(content)
                    # Sauvegarde automatique
                    self.save_content(content, output_dir)
                    logger.info(f"Succès: {url}")
                else:
                    logger.warning(f"Échec: {url}")
            except Exception as e:
                logger.error(f"Erreur lors du scraping de {url}: {str(e)}")
                continue
        
        return results
    
    def close(self):
        """Fermeture de la session"""
        self.session.close()

def main():
    """Fonction principale de démonstration"""
    # Exemples d'URLs pour chaque type de site
    test_urls = [
        "https://www.legifrance.gouv.fr/affichTexte.do?cidTexte=JORFTEXT000000000000000",
        "https://bofip.impots.gouv.fr/bofip/",
        "https://www.conseil-etat.fr/",
        "https://www.courdecassation.fr/",
        "https://www.conseil-constitutionnel.fr/",
        "https://www.assemblee-nationale.fr/",
        "https://www.senat.fr/",
        "https://www.fiscalonline.com/"
    ]
    
    scraper = LegalScraper(delay=2.0)  # Délai de 2 secondes entre les requêtes
    
    try:
        print("Démarrage du scraping des sites juridiques et fiscaux...")
        print("=" * 60)
        
        for url in test_urls:
            print(f"\nScraping de: {url}")
            content = scraper.scrape_url(url)
            
            if content:
                print(f"✓ Succès - Titre: {content.title[:100]}...")
                print(f"  Type: {content.site_type}")
                print(f"  Longueur du contenu: {len(content.content)} caractères")
                
                # Sauvegarde
                output_path = scraper.save_content(content)
                print(f"  Sauvegardé dans: {output_path}")
            else:
                print("✗ Échec du scraping")
        
        print("\n" + "=" * 60)
        print("Scraping terminé !")
        
    except KeyboardInterrupt:
        print("\nScraping interrompu par l'utilisateur")
    except Exception as e:
        print(f"Erreur générale: {str(e)}")
    finally:
        scraper.close()

if __name__ == "__main__":
    main()

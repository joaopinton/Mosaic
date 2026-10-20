import os
import json
import sqlite3
import re
import shutil

WORKSPACE_DIR = r"e:\padova moto days 2026"
THUMBS_DIR = os.path.join(WORKSPACE_DIR, "thumbnails")
MANIFEST_PATH = os.path.join(WORKSPACE_DIR, "manifest.json")
FEATURES_PATH = os.path.join(WORKSPACE_DIR, "features_cache.json")
DB_PATH = os.path.join(WORKSPACE_DIR, "padova_moto_days.db")

def sanitize_name(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9_]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text

def determine_metadata(orig_name, idx_num, features):
    # colors from features
    colors = features.get("colors", ["Multicolore"])
    color_str = "-".join([c.lower() for c in colors[:2]])
    if not color_str:
        color_str = "colore"

    # Default values
    cat = "Esposizione & Evento"
    brand = "Generico"
    model = ""
    shot = "Panoramica Stand"
    desc = "Scatto fotografico durante la manifestazione Padova Moto Days 2026."
    tags = ["padova moto days", "fiera", "motori", "2026"]
    short_desc = "evento"

    # 1. Poster / Locandina
    if orig_name.endswith('.png') or "2d858a5e" in orig_name:
        cat = "Locandina & Grafica"
        brand = "Padova Moto Days"
        model = "Edizione 2026"
        shot = "Grafica Ufficiale"
        desc = "Locandina promozionale ufficiale dell'evento Padova Moto Days 2026 presso l'Ex Jutificio di Piazzola sul Brenta."
        tags = ["locandina", "poster", "padova moto days", "piazzola sul brenta", "programma"]
        short_desc = "locandina_ufficiale_evento"
        return cat, brand, model, shot, desc, tags, short_desc, "0000"

    num = idx_num
    num_str = f"{num:04d}"

    # 2. Block 1 (0001 - 0024): Ducati DAT Owners Club
    if 1 <= num <= 24:
        cat = "Moto Sportiva"
        brand = "Ducati"
        shot = "Esposizione Stand"
        tags = ["ducati", "moto sportiva", "superbike", "stand", "dat owners club"]
        if num in (1, 2, 4, 10, 15):
            model = "998s / 996"
            desc = "Ducati serie Tamburini (998s/996) rossa da competizione esposta presso lo stand del club DAT Owners."
            short_desc = f"ducati_998s_stand_dat_{color_str}"
        elif num in (3, 5, 6, 7):
            model = "Superbike Corsa"
            desc = "Moto Ducati Superbike con livrea da gara su moquette rossa nello spazio espositivo."
            short_desc = f"ducati_superbike_corsa_{color_str}"
        elif num in (8, 9, 11, 12, 13, 14):
            model = "748 / 996 Biposto"
            desc = "Panoramica di moto Ducati sportive d'epoca con cartelli tecnici informativi."
            short_desc = f"ducati_sportive_collezione_{color_str}"
        else:
            model = "Superbike Special"
            desc = "Dettagli ed esemplari speciali Ducati esposti al pubblico degli appassionati."
            short_desc = f"ducati_stand_esposizione_{color_str}"

    # 3. Block 1b (0025 - 0030): Stand Dainese & Ferrari 296 GTB / Panigale V4
    elif 25 <= num <= 30:
        cat = "Supercar & Moto"
        brand = "Ferrari & Ducati"
        model = "296 GTB & Panigale V4 R"
        shot = "Vista 3/4 Anteriore"
        desc = "Stand Dainese e Banca Mediolanum con supercar Ferrari 296 GTB rossa affiancata a Ducati Panigale V4 R Tricolore."
        tags = ["ferrari", "ducati", "panigale", "296 gtb", "dainese", "mediolanum", "supercar"]
        short_desc = f"ferrari_296gtb_ducati_panigale_{color_str}"

    # 4. Block 1c (0031 - 0044): Stand D Garage Parts Ducati
    elif 31 <= num <= 44:
        cat = "Moto Sportiva"
        brand = "Ducati"
        model = "Panigale V4 Superbike"
        shot = "Esposizione Stand"
        desc = "Stand D Garage Parts con moto Ducati Panigale da corsa numero 1 e componenti speciali racing."
        tags = ["ducati", "d garage parts", "panigale v4", "racing", "ricambi"]
        short_desc = f"ducati_d_garage_parts_{color_str}"

    # 5. Block 1d (0045 - 0074): Vespa Club Roncajette & Scooter Classici
    elif 45 <= num <= 74:
        cat = "Vespa & Scooter Epoca"
        brand = "Piaggio Vespa"
        tags = ["vespa", "piaggio", "scooter epoca", "vespa club", "custom"]
        if num in (50, 51, 52, 53):
            model = "Special Custom Bitubo"
            shot = "Vista 3/4 Laterale"
            desc = "Vespa Special grigia personalizzata con ammortizzatore anteriore Bitubo rosso e borsa serbatoio."
            short_desc = f"vespa_special_custom_bitubo_{color_str}"
        elif num in (45, 46, 47, 48, 49):
            model = "Primavera / PX Epoca"
            shot = "Esposizione Stand"
            desc = "Scooter Vespa Piaggio classici restaurati in mostra presso il Vespa Club Roncajette."
            short_desc = f"vespa_club_esposizione_{color_str}"
        elif num in (54, 55, 56, 58, 59, 61, 63):
            model = "Scooter Vintage"
            shot = "Vista Profilo"
            desc = "Esemplari storici Piaggio Vespa con adesivi raduni e allestimenti da viaggio."
            short_desc = f"vespa_vintage_raduno_{color_str}"
        else:
            model = "Collezione Scooter"
            shot = "Panoramica Stand"
            desc = "Esposizione tematica di modelli storici Vespa con livree e accessori originali dell'epoca."
            short_desc = f"vespa_collezione_stand_{color_str}"

    # 6. Block 1e (0075 - 0085): Tavolo Lego Technic Supercar
    elif 75 <= num <= 85:
        cat = "Lego & Modellismo"
        brand = "Lego Technic"
        model = "Ferrari F40 & Supercar"
        shot = "Piano Ravvicinato Tavolo"
        desc = "Tavolo espositivo con modelli da collezione Lego Technic: Ferrari F40 rossa, supercar blu, auto da corsa e riviste Lego."
        tags = ["lego", "lego technic", "modellismo", "ferrari f40", "brick"]
        short_desc = f"lego_technic_supercar_tavolo_{color_str}"

    # 7. Block 1f & 2 (0086 - 0111): Stand Moto Club Touring Montegrotto & Moto d'Epoca
    elif 86 <= num <= 111:
        cat = "Moto d'Epoca"
        brand = "Moto Guzzi & Epoca"
        model = "Moto Storiche Restaurate"
        shot = "Esposizione Stand"
        desc = "Stand Moto Club Touring Montegrotto Terme con moto d'epoca Moto Guzzi, coppa trofeo dorata e pannello restauri."
        tags = ["moto epoca", "moto guzzi", "montegrotto terme", "restauro", "storia"]
        short_desc = f"moto_guzzi_epoca_montegrotto_{color_str}"

    # 8. Block 3 (0112 - 0138): Stand Kart & Minimoto (CKR)
    elif 112 <= num <= 138:
        cat = "Kart & Minimoto"
        brand = "CKR Racing"
        model = "Go-Kart Competizione"
        shot = "Inquadratura Frontale / 3/4"
        desc = "Go-kart da competizione CKR Polenta e Motori con carenatura blu e bianca su cavalletto di supporto."
        tags = ["kart", "go-kart", "ckr", "polenta e motori", "racing", "minimoto"]
        short_desc = f"gokart_ckr_racing_stand_{color_str}"

    # 9. Block 4a (0139 - 0144): Stand BMW Motorrad R1250 GS
    elif 139 <= num <= 144:
        cat = "Moto Turismo"
        brand = "BMW Motorrad"
        model = "R 1250 GS Adventure"
        shot = "Esposizione Stand"
        desc = "Stand con maxi enduro BMW R1250 GS e banner 'Moto Incontro Sagra di San Giacomo'."
        tags = ["bmw", "r1250gs", "adventure", "moto turismo", "sagra san giacomo"]
        short_desc = f"bmw_r1250gs_adventure_stand_{color_str}"

    # 10. Block 4b (0145 - 0147): Area Padova Brick Lego
    elif 145 <= num <= 147:
        cat = "Lego & Animazione"
        brand = "Padova Brick"
        model = "Supercar Technic Bimbi"
        shot = "Ritratto Espositori"
        desc = "Staff dell'Area Gioco Bimbi Padova Brick davanti al tavolo con supercar Lego Technic (Lamborghini verde, Ferrari rossa)."
        tags = ["padova brick", "lego", "area bimbi", "animazione", "supercar"]
        short_desc = f"padova_brick_area_bimbi_lego_{color_str}"

    # 11. Block 4c (0148 - 0174): Raduno Supercar Piazzale Esterno
    elif 148 <= num <= 174:
        tags = ["supercar", "raduno", "ferrari", "mclaren", "porsche", "piazzale"]
        if num in (150, 151, 152, 153, 154):
            cat = "Supercar"
            brand = "Ferrari"
            model = "488 Pista"
            shot = "Vista 3/4 Anteriore"
            desc = "Ferrari 488 Pista Rosso Corsa con livrea a strisce centrali nel cortile esterno del raduno."
            short_desc = f"ferrari_488_pista_rosso_cortile_{color_str}"
        elif num in (155, 156, 157):
            cat = "Supercar"
            brand = "Ferrari"
            model = "488 Pista Coppia"
            shot = "Frontale Coppia"
            desc = "Coppia di Ferrari 488 Pista affiancate: Grigio Silverstone con targa M OR 488 e Rosso Corsa con targa GA 488NW."
            short_desc = f"ferrari_488_pista_coppia_grigio_rosso_{color_str}"
        elif num in (164, 165, 166):
            cat = "Dettaglio Tecnico"
            brand = "Ferrari"
            model = "488 Pista Dettaglio Ruota"
            shot = "Macro Ruota & Freno"
            desc = "Dettaglio ravvicinato della ruota anteriore Ferrari 488 Pista con cerchio nero satinato, pinza freno Brembo gialla e disco carboceramico."
            short_desc = f"ferrari_488_dettaglio_ruota_freno_giallo_{color_str}"
        elif num in (167, 168, 169, 170):
            cat = "Supercar"
            brand = "Supercar Gathering"
            model = "McLaren, Ferrari, Porsche"
            shot = "Panoramica Piazzale"
            desc = "Panoramica grandangolare del piazzale supercar con McLaren 720S arancione a porte aperte, McLaren GT, Ferrari 348 e Ferrari 488."
            short_desc = f"raduno_supercar_panoramica_piazzale_{color_str}"
        else:
            cat = "Supercar"
            brand = "Ferrari / McLaren"
            model = "Supercar Raduno"
            shot = "Vista Esterna"
            desc = "Supercar ad alte prestazioni esposte all'aperto circondate dal pubblico di appassionati."
            short_desc = f"supercar_esposizione_aperto_{color_str}"

    # 12. Block 4d (0175 - 0187): Stand Gobbi Moto & MV Agusta
    elif 175 <= num <= 187:
        cat = "Moto Sportiva"
        brand = "MV Agusta & Gobbi Moto"
        model = "Brutale Naked"
        shot = "Esposizione su Pedana"
        desc = "Moto naked MV Agusta Brutale argento con telaio a traliccio rosso esposta su pedana in alluminio mandorlato da Gobbi Moto."
        tags = ["mv agusta", "brutale", "gobbi moto", "naked", "telaio rosso"]
        short_desc = f"mv_agusta_brutale_gobbi_moto_{color_str}"

    # 13. Block 4e (0188 - 0193): Area Biliardino Calcio Padova
    elif 188 <= num <= 193:
        cat = "Calcio Balilla & Svago"
        brand = "Calcio Padova"
        model = "Biliardino Gigante 8 Giocatori"
        shot = "Inquadratura dall'Alto"
        desc = "Calcio balilla maxi per partite a squadre personalizzato con logo ufficiale Calcio Padova 1910."
        tags = ["calcio balilla", "biliardino", "calcio padova", "area gioco", "svago"]
        short_desc = f"calcio_balilla_gigante_padova_{color_str}"

    # 14. Block 5 (0194 - 0227): Cerimonia Ufficiale di Inaugurazione & Taglio del Nastro
    elif 194 <= num <= 227:
        cat = "Inaugurazione Ufficiale"
        brand = "Comune & Organizzazione"
        model = "Taglio Nastro & Autorità"
        tags = ["inaugurazione", "taglio nastro", "sindaco", "autorita", "discorso", "cerimonia"]
        if 198 <= num <= 206:
            shot = "Taglio del Nastro"
            desc = "Cerimonia ufficiale del taglio del nastro tricolore con il Sindaco di Piazzola sul Brenta, la presentatrice e gli organizzatori."
            short_desc = f"inaugurazione_taglio_nastro_sindaco_{color_str}"
        elif 211 <= num <= 227:
            shot = "Discorso Autorità"
            desc = "Discorso inaugurale degli organizzatori e delle autorità locali al microfono per l'apertura ufficiale della fiera."
            short_desc = f"inaugurazione_discorso_autorita_{color_str}"
        else:
            shot = "Gruppo Istituzionale"
            desc = "Momento istituzionale della delegazione promotrice di Padova Moto Days 2026."
            short_desc = f"inaugurazione_gruppo_autorita_{color_str}"

    # 15. Block 6 (0228 - 0232): Speaker Radiofonica Live
    elif 228 <= num <= 232:
        cat = "Media & Radio"
        brand = "Radio FM 107.7"
        model = "Diretta Radio Live"
        shot = "Ritratto Mezzo Busto"
        desc = "Presentatrice e speaker radiofonica in diretta radio con microfono rosso davanti al totem dell'emittente FM 107.7."
        tags = ["radio", "diretta radio", "speaker", "intervista", "media partner"]
        short_desc = f"speaker_radio_diretta_fm107_{color_str}"

    # 16. Block 7a (0233 - 0244): Stand 2RS Minimoto School
    elif 233 <= num <= 244:
        cat = "Scuola Minimoto & Stand"
        brand = "2RS Minimoto School"
        model = "Totem Multimediale & Zip Racing"
        shot = "Interazione Visitatori"
        desc = "Stand della 2RS Minimoto School con totem touch-screen informativo, scooter Piaggio Zip da gara e tuta da corsa bimbo."
        tags = ["2rs minimoto", "scuola moto", "totem", "zip racing", "stand"]
        short_desc = f"stand_2rs_minimoto_school_{color_str}"

    # 17. Block 7b (0245 - 0251): Supercar Focus McLaren 720S
    elif 245 <= num <= 251:
        tags = ["supercar", "mclaren", "720s", "ruota", "freno", "arancione"]
        if num == 250:
            cat = "Dettaglio Tecnico"
            brand = "McLaren"
            model = "720S Dettaglio Ruota"
            shot = "Macro Ruota & Freno"
            desc = "Dettaglio ravvicinato della ruota in lega McLaren 720S con pinza freno arancione con marchio McLaren, disco carboceramico e gomma Pirelli PZero."
            short_desc = f"mclaren_720s_dettaglio_ruota_freno_arancio_{color_str}"
        elif num in (248, 249):
            cat = "Supercar"
            brand = "McLaren & Ferrari"
            model = "720S & Parco Supercar"
            shot = "Vista 3/4 Posteriore"
            desc = "Inquadratura 3/4 posteriore delle supercar allineate: McLaren 720S arancione, McLaren GT nera, Ferrari 348 e 488 Pista."
            short_desc = f"mclaren_720s_parco_supercar_posteriore_{color_str}"
        else:
            cat = "Supercar"
            brand = "McLaren"
            model = "720S Spider/Coupe"
            shot = "Vista Anteriore / Laterale"
            desc = "McLaren 720S arancione metallizzato con portiere con apertura ad ali di farfalla sollevate."
            short_desc = f"mclaren_720s_porte_aperte_{color_str}"

    # 18. Block 7c (0252 - 0263): Shooting Modella & London Cab Taxi Inglese
    elif 252 <= num <= 263:
        cat = "Modella & Taxi Storico"
        brand = "Austin London Cab"
        model = "FX4 Taxi Inglese"
        shot = "Shooting Fotografico Posato"
        desc = "Shooting fotografico con la modella in tuta racing a scacchi e stivali neri a bordo e all'esterno del celebre London Cab taxi nero."
        tags = ["modella", "shooting", "london cab", "taxi inglese", "austin fx4", "hostess"]
        short_desc = f"modella_taxi_londinese_austin_{color_str}"

    # 19. Block 8, 9, 10 (0264 - 0274): Padiglione KTM & Husqvarna
    elif 264 <= num <= 274:
        cat = "Moto Cross & Enduro"
        brand = "KTM & Husqvarna"
        model = "Gamma Enduro / Motard"
        shot = "Esposizione Salone"
        desc = "Ampio spazio espositivo ufficiale con striscioni KTM arancioni e Husqvarna blu, moto da cross, enduro e naked pronte gara."
        tags = ["ktm", "husqvarna", "enduro", "cross", "motard", "concessionaria"]
        short_desc = f"ktm_husqvarna_padiglione_moto_{color_str}"

    # 20. Block 11 & 12 (0278 - 0291): Stand Auto Salmaso Ferrari 296 GTB Blu
    elif 278 <= num <= 291:
        cat = "Supercar"
        brand = "Ferrari & Auto Salmaso"
        model = "296 GTB Blu Notte"
        shot = "Vista Frontale / 3/4"
        desc = "Supercar ibrida Ferrari 296 GTB di colore blu scuro metallizzato esposta presso lo stand Auto Salmaso."
        tags = ["ferrari", "296 gtb", "auto salmaso", "supercar", "blu notte"]
        short_desc = f"ferrari_296gtb_blu_auto_salmaso_{color_str}"

    # 21. Block 13 (0292 - 0296): Premiazione Torneo Calcio Balilla
    elif 292 <= num <= 296:
        cat = "Premiazione Torneo"
        brand = "Padova Moto Days"
        model = "Torneo Calcio Balilla"
        shot = "Foto di Gruppo Vincitori"
        desc = "Cerimonia di premiazione del Torneo di Calcio Balilla Padova Moto Days con i primi 3 classificati premiati con coppe e targhe ufficiali."
        tags = ["premiazione", "calcio balilla", "vincitori", "coppe", "torneo"]
        short_desc = f"premiazione_torneo_calcio_balilla_{color_str}"

    # 22. Block 14 - 18 (0297 - 0321): Padiglioni Serali e Staff di Gruppo
    elif 297 <= num <= 321:
        tags = ["staff", "padova moto days", "serata", "organizzatori", "gruppo"]
        if 313 <= num <= 317:
            cat = "Staff & Organizzazione"
            brand = "Padova Moto Days"
            model = "Team Organizzativo"
            shot = "Foto Ufficiale di Gruppo"
            desc = "Foto ricordo ufficiale dell'intero staff e direttivo organizzativo di Padova Moto Days schierato davanti al photocall istituzionale."
            short_desc = f"staff_organizzativo_foto_ufficiale_{color_str}"
        else:
            cat = "Padiglione & Visitatori"
            brand = "Padova Moto Days"
            model = "Serata Fiera"
            shot = "Atmosfera Stand"
            desc = "Scatti serali tra gli stand del salone fieristico con visitatori e appassionati di motori."
            short_desc = f"padiglione_serale_visitatori_{color_str}"

    # 23. Block 19 (0322 - 0357): Gran Gala Premiazioni Ufficiali sul Palco
    else:
        cat = "Premiazione Ufficiale & Stage"
        brand = "Padova Moto Days Awards"
        model = "Attestati di Eccellenza & Partecipazione"
        shot = "Consegna Premio sul Palco"
        tags = ["premiazione", "attestato", "stage", "riconoscimento", "spumante", "pilota"]
        if 322 <= num <= 330:
            desc = "Consegna dell'Attestato di Eccellenza e bottiglia celebrativa a piloti ed espositori davanti al grande logo Padova Moto Days."
            short_desc = f"premiazione_attestato_eccellenza_{color_str}"
        elif 331 <= num <= 342:
            desc = "Riconoscimento ufficiale e consegna attestato per la scuola 2RS Minimoto School sul palco della manifestazione."
            short_desc = f"premiazione_2rs_minimoto_stage_{color_str}"
        else:
            desc = "Premiazione sul palco per Gobbi Moto e partner tecnici con consegna targa e attestato ufficiale."
            short_desc = f"premiazione_partner_gobbi_moto_{color_str}"

    return cat, brand, model, shot, desc, tags, short_desc, num_str

def main():
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
        
    with open(FEATURES_PATH, 'r', encoding='utf-8') as f:
        features = json.load(f)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"Catalogazione e rinomina per {len(manifest)} foto in corso...")
    
    renamed_count = 0
    updated_manifest = []
    
    for idx, item in enumerate(manifest):
        orig_name = item['original_name']
        f_feat = features.get(orig_name, {})
        
        # Determine number index
        match = re.search(r'(\d+)', orig_name)
        num_val = int(match.group(1)) if match else (idx + 1)
        
        cat, brand, model, shot, desc, tags, short_desc, num_str = determine_metadata(orig_name, num_val, f_feat)
        
        ext = os.path.splitext(orig_name)[1].lower()
        new_filename = f"{num_str}_{sanitize_name(short_desc)}{ext}"
        
        old_full_path = os.path.join(WORKSPACE_DIR, orig_name)
        new_full_path = os.path.join(WORKSPACE_DIR, new_filename)
        
        old_thumb_path = os.path.join(THUMBS_DIR, orig_name)
        new_thumb_path = os.path.join(THUMBS_DIR, new_filename)
        
        # Physical rename in folder
        if os.path.exists(old_full_path) and orig_name != new_filename:
            try:
                os.rename(old_full_path, new_full_path)
                renamed_count += 1
            except Exception as e:
                print(f"Errore rinomina {orig_name} -> {new_filename}: {e}")
                
        # Rename thumbnail as well
        if os.path.exists(old_thumb_path) and orig_name != new_filename:
            try:
                os.rename(old_thumb_path, new_thumb_path)
            except Exception as e:
                pass
                
        # Update manifest record
        item['current_name'] = new_filename
        item['current_path'] = new_full_path
        updated_manifest.append(item)
        
        # Save to SQLite
        vec_json = json.dumps(f_feat.get("vector", []))
        colors_str = ", ".join(f_feat.get("colors", []))
        tags_str = ", ".join(tags)
        
        cursor.execute("""
        INSERT INTO photos (
            original_name, new_name, relative_path, absolute_path,
            category, brand, model, shot_type, primary_colors,
            description, tags, width, height, size_bytes, sha256, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(original_name) DO UPDATE SET
            new_name=excluded.new_name,
            relative_path=excluded.relative_path,
            absolute_path=excluded.absolute_path,
            category=excluded.category,
            brand=excluded.brand,
            model=excluded.model,
            shot_type=excluded.shot_type,
            primary_colors=excluded.primary_colors,
            description=excluded.description,
            tags=excluded.tags,
            width=excluded.width,
            height=excluded.height,
            size_bytes=excluded.size_bytes,
            sha256=excluded.sha256,
            embedding=excluded.embedding;
        """, (
            orig_name, new_filename, new_filename, new_full_path,
            cat, brand, model, shot, colors_str,
            desc, tags_str, item.get('width'), item.get('height'),
            item.get('size_bytes'), item.get('sha256'), vec_json
        ))
        
    conn.commit()
    conn.close()
    
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(updated_manifest, f, indent=2, ensure_ascii=False)
        
    print(f"Completato! Rinominati {renamed_count} file.")
    print("Database SQLite aggiornato con successo.")

if __name__ == "__main__":
    main()

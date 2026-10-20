# Verifica finale (VERIFICATION)

Chiusura del lavoro con il metodo REA (skill `reverse-engineer-anything`):

> "Before finishing, revisit the original checklist. Mark each question as answered, partially answered, or unresolved."

Ogni riga del [FEATURE_LEDGER](FEATURE_LEDGER.md) ha un esito e la prova che lo dimostra. Una funzione è **risolta** solo se un test o un comando eseguito la dimostra; è **parziale** quando la logica è provata ma manca una prova su dati o ambiente reali; è **aperta** se non provata. Compilata il 19/10/2026.

## Come si rifà la verifica

```bash
.venv/Scripts/python -m pytest --cov=mosaic --cov-report=term-missing
```

```bash
cd frontend && npx vitest run && npx vue-tsc --noEmit && node scripts/lint-tokens.mjs
```

```bash
.venv/Scripts/python -m ruff check mosaic tests scripts && .venv/Scripts/python -m bandit -q -r mosaic -c pyproject.toml
```

Con un server avviato (`python -m mosaic serve --no-browser --port 8766`) si controllano gli endpoint reali con:

```bash
.venv/Scripts/python scripts/verify_http.py http://127.0.0.1:8766
```

## Risultati misurati

| Controllo | Risultato |
|---|---|
| `pytest` (Python) | **284 passati**, 0 falliti, 0 saltati |
| Copertura `mosaic/` | **90%** (5852 istruzioni, 605 non coperte); obiettivo ≥ 80% |
| `vitest` (frontend) | **66 passati** in 5 file (include axe-core sull'accessibilità) |
| `vue-tsc --noEmit` | 0 errori |
| Lint dei token | nessuna violazione |
| `ruff` / `bandit` | nessun avviso |
| Build frontend | JS principale 104 kB gzip (limite del progetto: 300) |
| `scripts/verify_http.py` su server reale con libreria di prova | **25/25** controlli |

Moduli con copertura più bassa, e perché: `assets.py` 22% (scarica da Internet: provato a mano, non in test), `jobs/handlers.py` 66%, `video/probe.py` 71%, `meta/exiftool.py` 73% e `thumbs/convert.py` 69% (rami per RAW e per errori di sistema).

## Prova end-to-end su libreria reale

Libreria mista di 22 foto uniche (JPEG, PNG, HEIC, video, Live Photo, stack con XMP/RAW sintetico), con `mosaic index` dalla riga di comando:

- indicizzate 22 foto, 2 duplicati rilevati, 1 video transcodificato in H.264;
- 22 analizzate (SigLIP 2 reale), 29 etichette assegnate, 3 album automatici;
- 22 foto rinominate con l'intero stack (XMP, `.mov`, `.CR2` compresi), poi `rollback --all`: **25 file ripristinati, 0 errori**, nomi originali identici;
- `search "lago"` → 7 risultati (ricerca semantica); `search "type:video"` → 2;
- `status`: asset tutti presenti.

Questa prova ha trovato due bug veri, ora corretti e coperti da test (`test_robustness.py`): esaurimento di RAM nell'embedding del testo (OpenBLAS) e stato rotto di `FaceEngine` dopo una `bad allocation`; più i titoli numerici usati come nome file (`test_identifier_like_titles_are_not_titles`).

## Esiti

Legenda: **R** risolto, **P** parziale, **A** aperto, **E** escluso per scelta dell'utente.

### Indicizzazione e libreria

| ID | Esito | Prova |
|---|---|---|
| IDX-01 | R | `test_png_and_nested_folders` |
| IDX-02 | R | `test_hidden_ignored_and_storage_dirs_are_skipped` |
| IDX-03 | R | `test_wrong_extension_is_logged_and_skipped`, `test_classify_and_sniff` |
| IDX-04 | R | `test_legacy_import_matches_by_sha256_and_marks_data_manual` (SHA-256), `test_second_run_skips_everything` (SHA-1) |
| IDX-05 | R | `test_second_run_skips_everything`, `test_rescan_reprocesses` |
| IDX-06 | R | `test_modified_sidecar_requeues_main_file` |
| IDX-07 | R | `test_stack_becomes_one_photo_with_four_files`, `test_raw_prefix_strips_all_known_extensions` |
| IDX-08 | R | `test_sequences_stack_when_enabled`, `test_sequences_only_stack_when_enabled` |
| IDX-09 | R | `test_group_key_apple_edit_and_case` |
| IDX-10 | R | `test_stack_of_four_files_has_jpeg_primary`, `test_raw_beats_video_and_heic_in_priority` |
| IDX-11 | R | `test_stack_by_xmp_uuid_merges_photos`, `test_stacking_by_date_gps_and_serial_only_when_enabled` |
| IDX-12 | R | `test_duplicate_copy_goes_to_duplicates_table`, `test_duplicates_exact_and_near` |
| IDX-13 | R | `test_moved_file_keeps_photo_and_metadata`, `test_reindex_after_rename_recognizes_files_and_creates_nothing_new` |
| IDX-14 | **P** | HEIC: `test_heic_is_converted_to_a_browser_preview`. RAW: solo l'errore gestito (`test_unreadable_raw_is_reported_and_does_not_stop_the_pipeline`); **nessun RAW vero provato** (nessuno disponibile) |
| IDX-15 | R | `test_worker_count_is_bounded`, `test_running_jobs_return_to_queue_after_crash`, `test_heavy_jobs_are_mutually_exclusive` |
| IDX-16 | R | `test_import_copies_with_date_and_crc_name_and_indexes`, `test_import_skips_files_already_in_the_library`, `test_import_move_removes_sources_after_success`, `test_import_never_overwrites_a_different_file_with_the_same_name`; CLI: `test_export_import_legacy_cleanup_and_import` |
| IDX-17 | R | `test_watcher_debounces_and_ignores_irrelevant_files`, `test_watcher_survives_failing_trigger` |
| IDX-18 | R | `test_cleanup_marks_restores_and_purges`, `test_deleted_file_is_marked_missing` |
| IDX-19 | R | `test_album_filter_manual_and_smart` e il filtro `folder:` in `test_filters` |
| IDX-20 | R | `test_no_hardcoded_drive_paths_in_the_code`, `test_index_with_a_path_registers_it_in_the_configuration` |

### Metadati

| ID | Esito | Prova |
|---|---|---|
| META-01 | R | `test_exiftool_reads_gps_title_and_caches_json`, `test_pillow_exif_fallback_reads_camera_and_date` |
| META-02 | R | `test_exiftool_reads_gps_title_and_caches_json` |
| META-03 | R | `test_xmp_sidecar_wins_over_embedded_and_blocks_llm` |
| META-04 | R | `test_takeout_json` |
| META-05 | R | `test_extract_date_fallback_chain`, `test_exif_date_with_offset_converts_to_utc`, `test_filename_dates` |
| META-06 | R | `test_lower_priority_source_never_overwrites_higher`, `test_set_taken_respects_priority`, `test_llm_never_overwrites_manual_text` |
| META-07 | R | `test_camera_normalization` |
| META-08 | R | `test_extract_basic_fields`, `test_extract_gps_zero_and_out_of_range_are_dropped` |
| META-09 | E | Richiede mappa e luoghi |
| META-10 | R | `test_manual_data_is_written_to_yaml_and_automatic_data_is_not`, `test_data_survives_a_lost_database`, `test_restore_never_overwrites_higher_priority_and_ignores_broken_files`, `test_backup_is_removed_when_manual_data_disappears` |
| META-11 | R | `test_mwg_regions_convert_center_to_top_left` (lettura); creazione dei marker XMP in `pipeline._apply_regions` |

### Miniature e colori

| ID | Esito | Prova |
|---|---|---|
| THB-01 | R | `test_generate_default_set_with_correct_dimensions`, `test_on_demand_large_size` |
| THB-02 | R | `test_generate_default_set_with_correct_dimensions` |
| THB-03 | R | `test_layout_of_cache_path`, `test_second_run_does_not_rewrite_files` |
| THB-04 | R | `test_exif_orientation_is_applied`, `test_png_with_alpha_is_flattened_on_white`, `test_small_images_are_never_upscaled` |
| THB-05 | R | `test_thumbnails_are_served_immutable_and_created_on_demand`; server reale: JPEG con `cache-control: immutable` |
| THB-06 | R | `test_dominant_colors_of_two_tone_image` |
| THB-07 | R | `test_color_names` |

### Intelligenza artificiale

| ID | Esito | Prova |
|---|---|---|
| AI-01 | R | `test_siglip_embeddings_are_normalized_and_color_aware`, `test_analyze_end_to_end_on_real_model` (modello reale) |
| AI-02 | R | `test_italian_and_english_queries_agree`, `test_hybrid_semantic_ranking_uses_vectors`; server reale: "paesaggio con un lago" → risultati |
| AI-03 | R | `test_vector_roundtrip_float16`, `test_search_orders_by_cosine_and_filters`, `test_shared_index_reloads_when_embeddings_change` |
| AI-04 | R | `test_deleted_photos_are_excluded_and_models_do_not_mix` |
| AI-05 | R | `test_registry_defaults_and_unknown_models`, `test_load_missing_file_returns_defaults` |
| AI-06 | **P** | Logica: `test_classifier_picks_the_matching_label_and_respects_limits`, `test_per_label_threshold_overrides_global`, `test_real_vocabulary_is_valid`. Sulla libreria di prova (22 foto) le etichette sono sensate, ma **le soglie non sono calibrate su una libreria reale ampia** |
| AI-07 | R | `test_real_vocabulary_is_valid` (vocabolario del dominio incluso) |
| AI-08 | R | `test_nsfw_three_valued_result`, `test_nsfw_is_unavailable_when_model_is_missing` |
| AI-09 | R | Rilevamento: `test_face_engine_loads_and_finds_nothing_in_noise`, `test_face_engine_recovers_after_a_failed_initialisation`. **Sul DB reale** (202 foto): 382 volti rilevati con YuNet, 271 ora in un cluster (erano 90); volti grandi senza cluster 38 (erano 197) |
| AI-10 | R | `test_face_embedding_is_normalized_128d` (SFace), `test_arcface_embeddings_are_512d_normalized_and_batched`, `test_arcface_flip_changes_the_vector_but_keeps_it_normalized`, `test_align_112_matches_opencv_align_crop` (ordine dei punti di YuNet), `test_align_112_maps_landmarks_onto_the_reference_template`. Taratura `scripts/face_eval.py --compare` su 92 volti nominati (9 persone): TAR a FAR 0,1% / 1% = 96,5% / 99,2% con SFace, 100% / 100% con ArcFace (`test_face_eval.py` prova le metriche) |
| AI-11 | R | `test_dbscan_finds_three_clusters_and_noise`, `test_cluster_faces_returns_centroid_and_bounded_radius`, `test_too_wide_group_is_split_or_discarded`, `test_clusters_are_created_for_groups_of_at_least_three`, `test_three_faces_are_enough_for_a_cluster`, `test_two_faces_form_a_cluster_only_in_the_retry_round`, `test_profile_faces_do_not_found_clusters_but_are_recognized_afterwards`, `test_frontal_accepts_straight_faces_and_rejects_profiles`. Sul DB reale: 27 cluster, nessuno con due persone |
| AI-12 | R | `test_match_accepts_within_limit_and_rejects_outside`, `test_match_is_ambiguous_between_different_people_close_together`, `test_new_faces_match_existing_cluster_without_creating_a_new_one`, `test_single_named_face_names_a_lookalike_without_a_cluster`, `test_ambiguous_match_between_two_people_becomes_a_suggestion`, `test_automatic_names_are_never_used_as_references` |
| AI-13 | R | `test_collision_unassigns_conflicting_markers_and_narrows_radius`, `test_name_given_to_one_face_is_propagated_by_consensus`, `test_orphan_markers_of_missing_files_are_removed`, `test_clusters_without_markers_are_deleted`, `test_reembed_keeps_subject_and_name_and_drops_old_clusters`, `test_reembed_groups_markers_of_a_photo_in_one_batch`, `test_unique_per_photo_keeps_the_closest_automatic_name`, `test_unique_per_photo_never_touches_manual_names`; suggerimenti: `test_intermediate_distance_creates_a_suggestion_not_a_name`, `test_rejected_suggestion_is_not_proposed_again`, `test_confirm_suggestion_names_the_face_as_manual_and_clears_the_suggestion`, `test_migration_adds_suggestion_columns_to_a_version_1_database`, `test_bulk_confirm_endpoint_confirms_valid_uids_and_reports_the_rest`, scheda «Da confermare» in `components.test.ts` (PeoplePage). Sul DB reale 52 nomi automatici (controllo a vista coerente) e 4 suggerimenti |
| AI-14 | R | `test_people_actions`, `test_people_flow` |
| AI-15 | R | `test_pairwise_above_finds_near_duplicates`, `test_hamming_pairs_and_signed_hashes`, `test_group_pairs_merges_transitively` |
| AI-16 | **P** | Il ripiego su CPU è provato (`test_dml_request_falls_back_to_cpu_when_unavailable`); **DirectML su MX130 non verificato** |

### Video e Live Photo

| ID | Esito | Prova |
|---|---|---|
| VID-01 | R | `test_probe_reads_stream_properties`, `test_probe_returns_none_for_non_video` |
| VID-02 | R | `test_poster_is_a_valid_jpeg`, `test_poster_time_is_a_tenth_capped_at_ten_seconds`, `test_video_gets_poster_duration_and_type` |
| VID-03 | R | `test_non_web_video_is_transcoded_once_to_h264_faststart`, `test_large_video_is_scaled_down_to_1920`, `test_transcode_failure_returns_none_and_leaves_no_partial_file`; libreria reale: mpeg4 → H.264 |
| VID-04 | R | `test_video_streaming_with_range_and_transcode_flow`; server reale: 206 con `video/*` |
| VID-05 | R | `test_short_video_next_to_image_makes_live_photo`, `test_long_video_next_to_image_is_not_live`, `test_live_photo_video_is_served` |
| VID-06 | R | `test_google_motion_photo_is_extracted`, `test_motion_video_extraction_validates_and_bounds` |
| VID-07 | R | `test_animated_gif_is_detected` |
| VID-08 | E | Nessuna accelerazione hardware (MX130 senza NVENC) |

### LLM locale

| ID | Esito | Prova |
|---|---|---|
| LLM-01 | **P** | Server e protocollo provati con un llama finto (`test_server_starts_serves_and_stops`, `test_describe_success_sends_image_and_language`); il modello vero (Qwen2.5-VL-3B) è stato provato **a mano su un'immagine**, ma `mosaic caption` su più foto reali non è stato rieseguito dopo le ultime modifiche. Tempi su CPU non misurati su una libreria vera |
| LLM-02 | R | `test_describe_success_sends_image_and_language` |
| LLM-03 | R | `test_extract_json_variants`, `test_describe_retries_once_with_stricter_prompt`, `test_describe_gives_up_after_two_bad_answers`, `test_fenced_json_answers_are_accepted` |
| LLM-04 | R | `test_caption_job_fails_clearly_when_llm_is_not_installed`, `test_photos_with_xmp_title_and_caption_are_skipped` |
| LLM-05 | R | `test_idle_server_shuts_down_but_not_while_busy`, `test_server_that_dies_at_startup_raises`, `test_heavy_jobs_are_mutually_exclusive`, `test_server_output_goes_to_a_log_file`, `test_default_threads_use_physical_cores`, `test_caption_restarts_the_server_once_after_a_crash`, `test_caption_job_stops_when_the_server_keeps_dying`, `test_connection_error_means_the_server_died_and_is_not_retried` |
| LLM-06 | R | `test_captions_are_applied_with_llm_source`, `test_llm_never_overwrites_manual_text` |
| LLM-07 | R | Velocità: `test_payload_constrains_output_with_json_schema`, `test_prepare_image_shrinks_the_long_side`, `test_eta_is_formatted_in_minutes_or_hours`. Nomi: `test_people_names_go_in_the_prompt_only_when_given`, `test_only_confirmed_names_are_read_left_to_right`. Parallelo e cache: `test_parallel_requests_overlap_and_caption_every_photo`, `test_parallel_crash_restarts_once_and_loses_no_photo`, `test_cancel_sends_no_new_photos_but_saves_those_in_flight`, `test_fixed_instructions_come_before_the_image_so_the_server_can_cache_them`, `test_timings_are_read_from_the_server_reply`. **Tempi reali** (19 ott 2026, `scripts/bench_captions.py`, 8 foto): Qwen3-VL 2B @448 15,2 s a foto con 2 richieste insieme (17,1 s con una), contro 65-90 s del 3B (vedi D-06) |
| LLM-08 | R | Contesto: `test_moment_is_described_in_italian`, `test_context_text_joins_only_what_is_known`, `test_stories_follow_album_membership`, `test_caption_prompt_receives_story_about_and_date`; rigenerazione dall'album: `test_album_caption_regenerates_only_that_albums_photos`; interfaccia: `albumpage.test.ts`. **Qualità delle didascalie con il contesto non ancora valutata su foto reali** |
| SRCH-FMT | R | `test_ratio_filter_has_a_six_percent_tolerance`, `test_size_filters`, `test_file_size_filter_in_megabytes`, `test_photos_without_dimensions_never_match_shape_filters` |
| LBL-PRUNE | R | `test_vocabulary_has_no_labels_describing_the_person_itself`, `test_stale_automatic_labels_are_removed_but_manual_ones_stay` |
| ORG-ROOT | R | `test_root_folder_becomes_an_album_even_when_photos_sit_directly_in_it`, `test_root_filter_selects_a_whole_root_with_its_subfolders` |

### Organizzazione

| ID | Esito | Prova |
|---|---|---|
| ORG-01 | R | `test_whole_stack_is_renamed_together`, `test_name_collision_gets_numeric_suffix_and_never_overwrites`, `test_failure_in_the_middle_of_a_stack_reverts_that_photo`, `test_target_created_after_planning_is_never_overwritten`, `test_recover_finalizes_interrupted_renames`, `test_dry_run_changes_nothing`; libreria reale: 22 foto con stack interi |
| ORG-02 | R | `test_rollback_restores_names_content_and_mtime_exactly`, `test_rollback_of_a_single_batch`; libreria reale: `rollback --all` ripristina 25 file |
| ORG-03 | R | `test_album_lifecycle`, `test_albums_crud_and_rules` |
| ORG-04 | R | `test_moments_create_update_and_remove`, `test_threshold_grows_with_library_size`, `test_automatic_albums_cannot_be_deleted_and_calendar_lists_months` |
| ORG-05 | E | Richiede i luoghi |
| ORG-06 | R | `test_batch_actions`, `test_default_visibility_hides_private_and_archived` |
| ORG-07 | R | `test_plain_jpeg_gets_thumbs_colors_quality_and_search_row` (qualità), `quality.py` coperto al 97% |
| ORG-08 | R | `test_patch_sets_manual_text_and_reindexes_search`, `test_labels_listing` |
| ORG-09 | **P** | `test_legacy_import_matches_by_sha256_and_marks_data_manual`, `test_real_legacy_catalog_is_readable_and_untouched` (legge il vero `padova_moto_days.db`). **Le foto originali (disco E:) non c'erano**, quindi l'abbinamento sulle 334 foto vere resta da fare |
| ORG-10 | R | `test_export_json_and_csv`, `test_export_downloads` |
| ORG-11 | R | `test_status_reports_counts_and_first_run` |

### Ricerca e similarità

| ID | Esito | Prova |
|---|---|---|
| SRC-01 | R | `test_parse_filters_text_quotes_and_negation`, `test_filters`, `test_parse_aliases_and_unknown_keys` |
| SRC-02 | R | `test_free_text_uses_full_text_search_without_embeddings`, `test_text_with_special_characters_is_safe` |
| SRC-03 | R | `test_order_count_offset_reverse`, `test_list_photos_default_filters_and_pagination` |
| SRC-04 | R | `test_labels_listing`; pagina Etichette (build e test componenti) |
| SRC-05 | R | `test_similar_ranks_by_composite_score_and_explains`, `test_time_closeness_and_jaccard`, `test_similar_endpoint_waits_for_embeddings_then_returns_hits` |
| SRC-06 | R | `test_similar_ranks_by_composite_score_and_explains`; server reale: ogni risultato ha i motivi |
| SRC-07 | **P** | Simili e casuale sono implementati (ordinamento `random`, pagina dettaglio); non c'è una pagina "Discover" dedicata |
| SRC-08 | R | `test_search_and_similar_report_clearly`, `test_index_runs_the_whole_chain_and_renames_then_rollback_restores` |

### Interfaccia e API

| ID | Esito | Prova |
|---|---|---|
| UI-01 | R | `PhotoGrid` in `components.test.ts` (scorrimento, selezione, caricamento) |
| UI-02 | R | `photodetail.test.ts` (15 test: titolo, preferita, tasto F, frecce, parole chiave, simili, volti, album, archivio, copia percorso, errori) |
| UI-03 | R | `photodetail.test.ts` › copia il percorso del file negli appunti e avvisa se non può |
| UI-04 | **P** | PhotoSwipe è integrato e il tipo-check passa, ma nei test è simulato: **lightbox non provata in un browser** |
| UI-05 | **P** | Pagine: Esplora, Ricerca, Collezioni (album, moments, cartelle, mesi), Album, Calendario, Persone, Etichette, Duplicati, Libreria, Impostazioni. Video, Live, Preferiti, Review, Privato e Archivio sono **filtri** di Esplora, non pagine separate |
| UI-06 | R | `bandit` pulito; nessun `v-html` né `innerHTML` in `frontend/src` (`grep` senza risultati) |
| UI-07 | R | `test_event_stream_yields_job_events_and_heartbeats`; `JobsBanner` nei test componenti |
| UI-08 | **P** | Token copiati da sift e `lint-tokens.mjs` senza violazioni; **confronto visivo con sift-argus-v3 non eseguito** |
| UI-09 | **P** | Sidebar con test funzionali e axe-core (`components.test.ts`); **aspetto e misure non controllati a schermo** |
| UI-10 | **P** | Durate e curve da token e `lib/motion.ts`; riduzione del movimento testata in `theme.test.ts`; **fluidità non osservata** |
| UI-11 | R | `@lucide/vue` con wrapper unico; build e `vue-tsc` senza errori |
| UI-12 | R | `theme.test.ts` (8 test: sistema, salvataggio, View Transitions, movimento ridotto, localStorage bloccato) |
| UI-13 | R | `lib.test.ts` (`installHotkeys`), `ShortcutsDialog` |
| API-01 | R | 30 test in `test_api.py` e 25 controlli su server reale; solo 127.0.0.1, host e origine verificati |

### Installazione e avvio

| ID | Esito | Prova |
|---|---|---|
| INS-01 | **P** | `Mosaic.bat` avvia l'app dalla cartella installata (host guard 400, origin guard 403, asset serviti); **il doppio clic sul `.lnk` non è stato provato** |
| INS-02 | R | Tutto gira da `.venv` e `runtime/`; nessun Docker |
| INS-03 | **P** | `status` mostra tutti gli asset presenti e verificati (SHA-256); `assets.py` ha il 22% di copertura nei test automatici (il download è stato provato a mano) |
| INS-04 | **P** | `setup.ps1` + `MosaicSetup.exe`: estrazione e installazione pulita in una cartella di prova con exit 0. **Installazione di Python da python.org e download dei modelli su un PC vergine non provati** |
| INS-05 | R | `SetupWizard` nei test componenti; `first_run` in `test_status_reports_counts_and_first_run` |

## Rischi aperti

1. **Spazio su C:.** Circa 7 GB liberi prima dei download; tutti gli asset ne occupano circa 4-6. Conviene installare sull'altro PC (`MosaicSetup.exe`) o spostare `data/`.
2. **Volti non provati su foto reali.** La logica è coperta con vettori sintetici; la soglia di rilevamento va controllata su una libreria con persone.
3. **RAW non provato.** Nessun file RAW vero disponibile; il ripiego sull'anteprima incorporata esiste ma è verificato solo sull'errore.
4. **Calibrazione delle etichette.** Soglie e z-score vanno ritoccati su una libreria reale (vocabolario in `models/labels_it.yaml`).
5. **YuNet `2023mar` invece di `2026may`.** Le soglie potrebbero richiedere una ricalibrazione.
6. **Tempo dell'LLM su CPU.** Prima delle accelerazioni: circa 100 s per foto (misurato su 2 foto reali). Dopo (19 ott 2026, 8 foto): Qwen3-VL 2B @448 con 2 richieste insieme, 15,2 s per foto, 0 errori, picco di RAM di llama-server circa 3,1 GB. Obiettivo 15-25 s raggiunto.
7. **Interfaccia mai vista a schermo in questa sessione** (regola del progetto: niente preview non richieste). La verifica è fatta con tipo-check, test componenti con axe-core, build e richieste HTTP reali.
8. **Licenza di Qwen2.5-VL (Qwen Research):** solo uso personale e di ricerca.
9. **Cartella `C:\mxtest`** creata dalla prova dell'installer: da cancellare a mano.

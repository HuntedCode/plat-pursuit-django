# Diff Details

Date : 2025-12-16 18:31:32

Directory c:\\Users\\Jlowe\\Desktop\\PlatPursuit

Total : 89 files,  4697 codes, 43 comments, 453 blanks, all 5193 lines

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details

## Files
| filename | language | code | comment | blank | total |
| :--- | :--- | ---: | ---: | ---: | ---: |
| [core/services/featured\_guide.py](/core/services/featured_guide.py) | Python | 17 | 0 | 2 | 19 |
| [core/services/featured\_profile.py](/core/services/featured_profile.py) | Python | 2 | 0 | 0 | 2 |
| [core/templatetags/custom\_filters.py](/core/templatetags/custom_filters.py) | Python | 47 | 0 | 11 | 58 |
| [core/views.py](/core/views.py) | Python | 13 | 0 | 1 | 14 |
| [plat\_pursuit/middleware.py](/plat_pursuit/middleware.py) | Python | 12 | 0 | 2 | 14 |
| [plat\_pursuit/settings.py](/plat_pursuit/settings.py) | Python | 28 | 2 | 5 | 35 |
| [plat\_pursuit/urls.py](/plat_pursuit/urls.py) | Python | 15 | 0 | 1 | 16 |
| [requirements.txt](/requirements.txt) | pip requirements | 8 | 0 | 0 | 8 |
| [static/css/input.css](/static/css/input.css) | PostCSS | 9 | 0 | 0 | 9 |
| [static/js/carousel.js](/static/js/carousel.js) | JavaScript | -1 | 0 | 0 | -1 |
| [static/js/guide\_scroller.js](/static/js/guide_scroller.js) | JavaScript | 68 | 0 | 8 | 76 |
| [static/js/profile\_scroller.js](/static/js/profile_scroller.js) | JavaScript | 72 | 0 | 8 | 80 |
| [templates/account/login.html](/templates/account/login.html) | HTML | 59 | 0 | 1 | 60 |
| [templates/base.html](/templates/base.html) | HTML | 3 | 0 | 0 | 3 |
| [templates/index.html](/templates/index.html) | HTML | 1 | 1 | 1 | 3 |
| [templates/partials/hotbar.html](/templates/partials/hotbar.html) | HTML | 282 | 0 | 25 | 307 |
| [templates/partials/index/featured-guide.html](/templates/partials/index/featured-guide.html) | HTML | 33 | 0 | 0 | 33 |
| [templates/partials/index/featured-profile.html](/templates/partials/index/featured-profile.html) | HTML | -3 | 0 | 0 | -3 |
| [templates/partials/navbar.html](/templates/partials/navbar.html) | HTML | 3 | 0 | 0 | 3 |
| [templates/trophies/badge\_detail.html](/templates/trophies/badge_detail.html) | HTML | 28 | 0 | 3 | 31 |
| [templates/trophies/badge\_list.html](/templates/trophies/badge_list.html) | HTML | 68 | 0 | 5 | 73 |
| [templates/trophies/game\_detail.html](/templates/trophies/game_detail.html) | HTML | 23 | 0 | 2 | 25 |
| [templates/trophies/guide\_list.html](/templates/trophies/guide_list.html) | HTML | 56 | 0 | 4 | 60 |
| [templates/trophies/partials/badge\_detail/badge\_detail\_concepts.html](/templates/trophies/partials/badge_detail/badge_detail_concepts.html) | HTML | 14 | 0 | 0 | 14 |
| [templates/trophies/partials/badge\_detail/badge\_detail\_header.html](/templates/trophies/partials/badge_detail/badge_detail_header.html) | HTML | 66 | 0 | 0 | 66 |
| [templates/trophies/partials/badge\_detail/badge\_detail\_items.html](/templates/trophies/partials/badge_detail/badge_detail_items.html) | HTML | 71 | 0 | 2 | 73 |
| [templates/trophies/partials/badge\_list/badge\_cards.html](/templates/trophies/partials/badge_list/badge_cards.html) | HTML | 31 | 0 | 1 | 32 |
| [templates/trophies/partials/badge\_list/badge\_list\_items.html](/templates/trophies/partials/badge_list/badge_list_items.html) | HTML | 35 | 0 | 1 | 36 |
| [templates/trophies/partials/game\_detail/community\_ratings.html](/templates/trophies/partials/game_detail/community_ratings.html) | HTML | 135 | 4 | 7 | 146 |
| [templates/trophies/partials/game\_detail/game\_detail\_header.html](/templates/trophies/partials/game_detail/game_detail_header.html) | HTML | 17 | 0 | 0 | 17 |
| [templates/trophies/partials/game\_detail/guide\_embed.html](/templates/trophies/partials/game_detail/guide_embed.html) | HTML | 8 | 0 | 0 | 8 |
| [templates/trophies/partials/guide\_list/featured\_guide.html](/templates/trophies/partials/guide_list/featured_guide.html) | HTML | 33 | 0 | 0 | 33 |
| [templates/trophies/partials/guide\_list/guide\_list\_items.html](/templates/trophies/partials/guide_list/guide_list_items.html) | HTML | 23 | 0 | 0 | 23 |
| [templates/trophies/partials/profile\_detail/badge\_filters.html](/templates/trophies/partials/profile_detail/badge_filters.html) | HTML | 13 | 0 | 0 | 13 |
| [templates/trophies/partials/profile\_detail/badge\_list\_items.html](/templates/trophies/partials/profile_detail/badge_list_items.html) | HTML | 32 | 0 | 0 | 32 |
| [templates/trophies/partials/profile\_detail/game\_filters.html](/templates/trophies/partials/profile_detail/game_filters.html) | HTML | 41 | 0 | 3 | 44 |
| [templates/trophies/partials/profile\_detail/game\_list\_items.html](/templates/trophies/partials/profile_detail/game_list_items.html) | HTML | 82 | 0 | 1 | 83 |
| [templates/trophies/partials/profile\_detail/profile\_detail\_header.html](/templates/trophies/partials/profile_detail/profile_detail_header.html) | HTML | 54 | 0 | 0 | 54 |
| [templates/trophies/partials/profile\_detail/trophy\_list\_items.html](/templates/trophies/partials/profile_detail/trophy_list_items.html) | HTML | 99 | 0 | 2 | 101 |
| [templates/trophies/partials/profile\_detail/trophy\_log\_filters.html](/templates/trophies/partials/profile_detail/trophy_log_filters.html) | HTML | 33 | 0 | 2 | 35 |
| [templates/trophies/partials/profile\_list/profile\_cards.html](/templates/trophies/partials/profile_list/profile_cards.html) | HTML | 12 | 0 | 0 | 12 |
| [templates/trophies/partials/trophy\_case/trophy\_case\_items.html](/templates/trophies/partials/trophy_case/trophy_case_items.html) | HTML | 59 | 0 | 0 | 59 |
| [templates/trophies/partials/trophy\_list/trophy\_list\_items.html](/templates/trophies/partials/trophy_list/trophy_list_items.html) | HTML | -3 | 0 | 0 | -3 |
| [templates/trophies/profile\_detail.html](/templates/trophies/profile_detail.html) | HTML | 49 | 0 | 2 | 51 |
| [templates/trophies/trophy\_case.html](/templates/trophies/trophy_case.html) | HTML | 72 | 0 | 6 | 78 |
| [trophies/admin.py](/trophies/admin.py) | Python | 42 | 0 | 8 | 50 |
| [trophies/apps.py](/trophies/apps.py) | Python | 2 | 0 | 0 | 2 |
| [trophies/forms.py](/trophies/forms.py) | Python | 87 | 0 | 7 | 94 |
| [trophies/management/commands/populate\_concept\_bg\_urls.py](/trophies/management/commands/populate_concept_bg_urls.py) | Python | 15 | 0 | 1 | 16 |
| [trophies/management/commands/populate\_concept\_icons.py](/trophies/management/commands/populate_concept_icons.py) | Python | 11 | 0 | 2 | 13 |
| [trophies/management/commands/populate\_title\_ids.py](/trophies/management/commands/populate_title_ids.py) | Python | 6 | 0 | 1 | 7 |
| [trophies/management/commands/recheck\_badges.py](/trophies/management/commands/recheck_badges.py) | Python | 44 | 0 | 10 | 54 |
| [trophies/management/commands/seed\_badges.py](/trophies/management/commands/seed_badges.py) | Python | 17 | 0 | 3 | 20 |
| [trophies/management/commands/test\_psn.py](/trophies/management/commands/test_psn.py) | Python | 1 | 0 | 0 | 1 |
| [trophies/migrations/0039\_profile\_user\_timezone.py](/trophies/migrations/0039_profile_user_timezone.py) | Python | 460 | 1 | 6 | 467 |
| [trophies/migrations/0040\_remove\_profile\_user\_timezone.py](/trophies/migrations/0040_remove_profile_user_timezone.py) | Python | 11 | 1 | 6 | 18 |
| [trophies/migrations/0041\_usertrophyselection.py](/trophies/migrations/0041_usertrophyselection.py) | Python | 48 | 1 | 6 | 55 |
| [trophies/migrations/0042\_alter\_usertrophyselection\_options\_and\_more.py](/trophies/migrations/0042_alter_usertrophyselection_options_and_more.py) | Python | 15 | 1 | 6 | 22 |
| [trophies/migrations/0043\_concept\_guide\_slug.py](/trophies/migrations/0043_concept_guide_slug.py) | Python | 12 | 1 | 6 | 19 |
| [trophies/migrations/0044\_userconceptrating.py](/trophies/migrations/0044_userconceptrating.py) | Python | 87 | 1 | 6 | 94 |
| [trophies/migrations/0045\_alter\_userconceptrating\_overall\_rating.py](/trophies/migrations/0045_alter_userconceptrating_overall_rating.py) | Python | 19 | 1 | 6 | 26 |
| [trophies/migrations/0046\_badge\_userbadge\_userbadgeprogress\_game\_is\_obtainable\_and\_more.py](/trophies/migrations/0046_badge_userbadge_userbadgeprogress_game_is_obtainable_and_more.py) | Python | 210 | 1 | 6 | 217 |
| [trophies/migrations/0047\_rename\_icon\_url\_badge\_icon\_badge\_base\_badge.py](/trophies/migrations/0047_rename_icon_url_badge_icon_badge_base_badge.py) | Python | 28 | 1 | 6 | 35 |
| [trophies/migrations/0048\_remove\_badge\_base\_badge.py](/trophies/migrations/0048_remove_badge_base_badge.py) | Python | 11 | 1 | 6 | 18 |
| [trophies/migrations/0049\_badge\_base\_badge.py](/trophies/migrations/0049_badge_base_badge.py) | Python | 20 | 1 | 6 | 27 |
| [trophies/migrations/0050\_userbadgeprogress\_required\_concepts\_and\_more.py](/trophies/migrations/0050_userbadgeprogress_required_concepts_and_more.py) | Python | 25 | 1 | 6 | 32 |
| [trophies/migrations/0051\_badge\_discord\_role\_id.py](/trophies/migrations/0051_badge_discord_role_id.py) | Python | 16 | 1 | 6 | 23 |
| [trophies/migrations/0052\_badge\_display\_series.py](/trophies/migrations/0052_badge_display_series.py) | Python | 12 | 1 | 6 | 19 |
| [trophies/migrations/0053\_badge\_earned\_count\_badge\_badge\_earned\_count\_idx.py](/trophies/migrations/0053_badge_earned_count_badge_badge_earned_count_idx.py) | Python | 18 | 1 | 6 | 25 |
| [trophies/migrations/0054\_concept\_release\_date\_and\_more.py](/trophies/migrations/0054_concept_release_date_and_more.py) | Python | 18 | 1 | 6 | 25 |
| [trophies/migrations/0055\_alter\_game\_concept.py](/trophies/migrations/0055_alter_game_concept.py) | Python | 19 | 1 | 6 | 26 |
| [trophies/migrations/0056\_featuredguide.py](/trophies/migrations/0056_featuredguide.py) | Python | 56 | 1 | 6 | 63 |
| [trophies/migrations/0057\_concept\_concept\_icon\_url.py](/trophies/migrations/0057_concept_concept_icon_url.py) | Python | 12 | 1 | 6 | 19 |
| [trophies/migrations/0058\_concept\_bg\_url.py](/trophies/migrations/0058_concept_bg_url.py) | Python | 12 | 1 | 6 | 19 |
| [trophies/migrations/0059\_profile\_sync\_progress\_target\_and\_more.py](/trophies/migrations/0059_profile_sync_progress_target_and_more.py) | Python | 41 | 1 | 6 | 48 |
| [trophies/migrations/0060\_alter\_profile\_sync\_status.py](/trophies/migrations/0060_alter_profile_sync_status.py) | Python | 21 | 1 | 6 | 28 |
| [trophies/mixins.py](/trophies/mixins.py) | Python | 24 | 0 | 2 | 26 |
| [trophies/models.py](/trophies/models.py) | Python | 188 | 0 | 29 | 217 |
| [trophies/psn\_manager.py](/trophies/psn_manager.py) | Python | 13 | 0 | 2 | 15 |
| [trophies/services/psn\_api\_service.py](/trophies/services/psn_api_service.py) | Python | -11 | 0 | -1 | -12 |
| [trophies/signals.py](/trophies/signals.py) | Python | 53 | 0 | 8 | 61 |
| [trophies/token\_keeper.py](/trophies/token_keeper.py) | Python | 79 | 0 | 11 | 90 |
| [trophies/utils.py](/trophies/utils.py) | Python | 133 | 8 | 24 | 165 |
| [trophies/views.py](/trophies/views.py) | Python | 555 | 4 | 95 | 654 |
| [users/admin.py](/users/admin.py) | Python | -16 | 0 | 3 | -13 |
| [users/forms.py](/users/forms.py) | Python | -1 | 0 | -1 | -2 |
| [users/migrations/0004\_customuser\_user\_timezone.py](/users/migrations/0004_customuser_user_timezone.py) | Python | 460 | 1 | 6 | 467 |
| [users/migrations/0005\_remove\_customuser\_users\_custo\_usernam\_a8ad03\_idx\_and\_more.py](/users/migrations/0005_remove_customuser_users_custo_usernam_a8ad03_idx_and_more.py) | Python | 16 | 1 | 6 | 23 |
| [users/models.py](/users/models.py) | Python | -11 | 0 | -1 | -12 |

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details
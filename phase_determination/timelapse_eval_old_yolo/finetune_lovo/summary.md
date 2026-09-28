# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **55.4%**
- точность ±1 фаза по порядку: 77.2%
- средняя уверенность: 70%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 74% | 100% | Earthwork 49%, Structural Frame 31% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 41% | 66% | Structural Frame 39%, Masonry 21% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 78% | 97% | Structural Frame 47%, Earthwork 25% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 5% | 40% | Foundation 36%, Finishing 33% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 91% | 100% | Structural Frame 59%, Foundation 23% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 1% | 14% | Preconstruction 27%, MEP 24% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 40% | 74% | Masonry 37%, Structural Frame 24% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 91% | 95% | Finishing 39%, Structural Frame 36% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 62% | 76% | Structural Frame 54%, Finishing 29% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 28% | 71% | Structural Frame 32%, Foundation 29% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 37% | 74% | Foundation 35%, Structural Frame 26% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 55% | 78% | Structural Frame 50%, Foundation 26% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 62% | 75% | Structural Frame 44%, Finishing 38% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 72% | 92% | Structural Frame 48%, Foundation 29% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 0% | 0% |
| Earthwork | 900 | 53% | 59% |
| Foundation | 1801 | 59% | 65% |
| Structural Frame | 3222 | 75% | 72% |
| Masonry | 72 | 1% | 0% |
| MEP | 87 | 0% | 0% |
| Finishing | 1569 | 49% | 51% |
| External Works | 716 | 15% | 58% |
| Commissioning | 167 | 0% | 0% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation |  |  | 88 | 95 |  |  |  |  |  |  |
| Earthwork | 97 | 126 | 477 | 182 | 18 |  |  |  |  |  |
| Foundation | 32 | 13 | 215 | 1061 | 399 | 16 | 51 | 1 | 13 |  |
| Structural Frame |  |  | 15 | 170 | 2416 | 235 | 27 | 292 | 52 | 15 |
| Masonry |  |  |  |  | 71 | 1 |  |  |  |  |
| MEP |  |  | 2 | 2 | 41 |  |  | 42 |  |  |
| Finishing |  |  | 9 | 43 | 281 | 264 | 32 | 767 | 12 | 161 |
| External Works |  |  |  | 78 | 139 | 35 | 25 | 239 | 105 | 95 |
| Commissioning |  |  |  |  |  |  |  | 167 |  |  |

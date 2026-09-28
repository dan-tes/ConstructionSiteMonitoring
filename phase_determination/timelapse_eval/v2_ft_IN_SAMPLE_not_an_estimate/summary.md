# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **77.7%**
- точность ±1 фаза по порядку: 92.1%
- средняя уверенность: 73%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 87% | 100% | Earthwork 50%, Structural Frame 24% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 60% | 77% | Finishing 46%, Structural Frame 19% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 80% | 98% | Structural Frame 53%, Earthwork 32% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 71% | 90% | Earthwork 31%, Structural Frame 31% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 88% | 98% | Structural Frame 60%, Foundation 22% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 74% | 81% | Foundation 47%, Structural Frame 26% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 88% | 98% | Structural Frame 63%, Finishing 24% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 92% | 96% | Finishing 46%, Structural Frame 28% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 78% | 92% | Structural Frame 58%, Finishing 38% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 80% | 89% | Foundation 53%, MEP 37% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 76% | 96% | External Works 44%, Foundation 24% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 63% | 87% | Finishing 41%, Foundation 25% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 78% | 90% | Structural Frame 38%, Commissioning 24% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 77% | 96% | Structural Frame 48%, Foundation 30% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 70% | 63% |
| Earthwork | 900 | 73% | 74% |
| Foundation | 1801 | 68% | 87% |
| Structural Frame | 3222 | 88% | 86% |
| Masonry | 72 | 1% | 5% |
| MEP | 87 | 92% | 64% |
| Finishing | 1569 | 86% | 70% |
| External Works | 716 | 49% | 78% |
| Commissioning | 167 | 95% | 52% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation | 5 | 129 | 49 |  |  |  |  |  |  |  |
| Earthwork | 82 | 77 | 653 | 75 |  |  | 13 |  |  |  |
| Foundation | 6 |  | 185 | 1219 | 322 | 19 | 11 | 16 | 23 |  |
| Structural Frame |  |  |  | 108 | 2828 |  |  | 259 | 27 |  |
| Masonry |  |  |  |  | 67 | 1 |  | 4 |  |  |
| MEP |  |  |  | 7 |  |  | 80 |  |  |  |
| Finishing |  |  |  |  | 54 |  |  | 1355 | 46 | 114 |
| External Works |  |  |  |  | 29 |  | 21 | 286 | 348 | 32 |
| Commissioning |  |  |  |  |  |  |  | 8 |  | 159 |

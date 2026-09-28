# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **38.6%**
- точность ±1 фаза по порядку: 62.4%
- средняя уверенность: 59%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 61% | 100% | Earthwork 45%, Foundation 37% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 30% | 61% | Structural Frame 23%, Finishing 22% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 62% | 96% | Structural Frame 43%, Foundation 27% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 47% | 77% | Foundation 50%, Structural Frame 39% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 86% | 96% | Structural Frame 54%, Foundation 24% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 0% | 29% | Foundation 39%, Preconstruction 25% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 18% | 25% | Structural Frame 37%, MEP 37% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 31% | 41% | Structural Frame 33%, Masonry 32% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 52% | 79% | Structural Frame 52%, Masonry 32% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 30% | 63% | Foundation 43%, Earthwork 31% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 21% | 53% | Structural Frame 40%, Masonry 15% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 17% | 40% | Structural Frame 36%, Finishing 21% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 35% | 53% | Structural Frame 33%, Masonry 26% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 58% | 86% | Structural Frame 44%, Masonry 21% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 0% | 0% |
| Earthwork | 900 | 47% | 48% |
| Foundation | 1801 | 33% | 47% |
| Structural Frame | 3222 | 64% | 67% |
| Masonry | 72 | 43% | 2% |
| MEP | 87 | 30% | 4% |
| Finishing | 1569 | 11% | 24% |
| External Works | 716 | 10% | 49% |
| Commissioning | 167 | 0% | 0% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation | 20 |  | 76 | 86 | 1 |  |  |  |  |  |
| Earthwork | 207 | 65 | 420 | 189 |  |  | 19 |  |  |  |
| Foundation | 93 | 46 | 340 | 598 | 429 | 99 | 71 | 88 | 37 |  |
| Structural Frame |  |  | 33 | 273 | 2052 | 287 | 400 | 177 |  |  |
| Masonry |  |  |  |  | 41 | 31 |  |  |  |  |
| MEP |  |  |  | 61 |  |  | 26 |  |  |  |
| Finishing |  |  |  | 52 | 259 | 874 | 128 | 166 | 36 | 54 |
| External Works |  |  |  |  | 285 | 124 | 41 | 135 | 71 | 60 |
| Commissioning |  |  |  |  |  | 29 |  | 138 |  |  |

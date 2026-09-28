# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **38.7%**
- точность ±1 фаза по порядку: 66.8%
- средняя уверенность: 61%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 65% | 100% | Earthwork 43%, Foundation 40% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 24% | 62% | Masonry 36%, Structural Frame 25% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 64% | 90% | Structural Frame 35%, Foundation 28% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 34% | 75% | Foundation 45%, Structural Frame 19% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 64% | 97% | Structural Frame 32%, Masonry 27% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 0% | 18% | Foundation 37%, Preconstruction 26% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 5% | 31% | Masonry 41%, Finishing 38% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 46% | 55% | Structural Frame 40%, Masonry 33% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 55% | 80% | Structural Frame 52%, Masonry 29% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 34% | 65% | Foundation 40%, Earthwork 31% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 23% | 64% | Structural Frame 26%, Foundation 22% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 39% | 82% | Masonry 28%, Structural Frame 16% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 34% | 50% | Structural Frame 40%, Masonry 34% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 63% | 87% | Structural Frame 42%, Foundation 29% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 0% | 0% |
| Earthwork | 900 | 48% | 51% |
| Foundation | 1801 | 44% | 54% |
| Structural Frame | 3222 | 54% | 70% |
| Masonry | 72 | 6% | 0% |
| MEP | 87 | 40% | 13% |
| Finishing | 1569 | 18% | 31% |
| External Works | 716 | 14% | 39% |
| Commissioning | 167 | 0% | 0% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation | 13 |  | 131 | 39 |  |  |  |  |  |  |
| Earthwork | 167 | 84 | 435 | 206 | 3 |  | 5 |  |  |  |
| Foundation | 93 | 11 | 266 | 798 | 355 | 65 | 39 | 86 | 75 | 13 |
| Structural Frame |  |  | 15 | 238 | 1725 | 766 | 91 | 268 | 83 | 36 |
| Masonry |  |  |  |  | 68 | 4 |  |  |  |  |
| MEP |  |  |  | 52 |  |  | 35 |  |  |  |
| Finishing |  |  |  | 53 | 223 | 957 | 60 | 276 |  |  |
| External Works |  |  |  | 100 | 86 | 120 | 48 | 133 | 101 | 128 |
| Commissioning |  |  |  |  |  | 36 |  | 131 |  |  |

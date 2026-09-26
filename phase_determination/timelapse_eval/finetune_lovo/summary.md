# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **54.4%**
- точность ±1 фаза по порядку: 76.4%
- средняя уверенность: 68%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 70% | 100% | Earthwork 52%, Structural Frame 33% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 50% | 82% | Finishing 39%, Structural Frame 26% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 68% | 100% | Structural Frame 53%, Foundation 27% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 11% | 40% | Structural Frame 35%, Foundation 29% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 91% | 100% | Structural Frame 57%, Foundation 23% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 1% | 18% | Earthwork 40%, MEP 28% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 60% | 76% | Structural Frame 76%, Masonry 9% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 89% | 99% | Finishing 44%, Structural Frame 35% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 56% | 70% | Structural Frame 51%, Finishing 32% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 12% | 54% | Earthwork 64%, Structural Frame 24% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 32% | 71% | Structural Frame 40%, Foundation 24% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 39% | 50% | Structural Frame 61%, Foundation 22% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 58% | 75% | Structural Frame 42%, Finishing 29% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 67% | 90% | Structural Frame 54%, Foundation 18% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 0% | 0% |
| Earthwork | 900 | 56% | 46% |
| Foundation | 1801 | 37% | 60% |
| Structural Frame | 3222 | 82% | 68% |
| Masonry | 72 | 1% | 1% |
| MEP | 87 | 0% | 0% |
| Finishing | 1569 | 53% | 52% |
| External Works | 716 | 11% | 34% |
| Commissioning | 167 | 2% | 1% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation |  |  | 91 | 92 |  |  |  |  |  |  |
| Earthwork | 60 | 54 | 507 | 214 | 47 |  | 18 |  |  |  |
| Foundation | 26 | 18 | 364 | 673 | 535 | 27 | 92 | 32 | 34 |  |
| Structural Frame |  |  | 27 | 143 | 2645 | 5 | 50 | 321 | 31 |  |
| Masonry |  |  |  |  | 71 | 1 |  |  |  |  |
| MEP |  |  | 60 |  | 27 |  |  |  |  |  |
| Finishing |  |  | 43 | 2 | 254 | 84 | 60 | 833 | 93 | 200 |
| External Works |  |  |  | 1 | 334 |  | 12 | 256 | 82 | 31 |
| Commissioning |  |  |  |  |  |  |  | 164 |  | 3 |

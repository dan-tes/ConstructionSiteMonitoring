# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **20.9%**
- точность ±1 фаза по порядку: 54.2%
- средняя уверенность: 82%
- для сравнения, константа «самая частая фаза эталона» (Structural Frame): 37.0%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 40% | 95% | Foundation 63%, Earthwork 23% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 17% | 56% | Foundation 69%, MEP 16% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 24% | 68% | Foundation 40%, MEP 31% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 18% | 81% | Foundation 88%, Earthwork 6% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 33% | 94% | Foundation 59%, Masonry 16% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 33% | 67% | Foundation 85%, Earthwork 6% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 3% | 17% | MEP 51%, Structural Frame 19% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 18% | 46% | Foundation 43%, MEP 21% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 21% | 51% | Masonry 39%, MEP 18% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 31% | 46% | Foundation 76%, Earthwork 15% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 20% | 35% | Foundation 42%, Earthwork 21% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 28% | 56% | Foundation 69%, MEP 13% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 14% | 34% | Foundation 34%, Masonry 19% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 31% | 86% | Foundation 57%, Structural Frame 17% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 18% | 11% |
| Earthwork | 900 | 23% | 37% |
| Foundation | 1801 | 60% | 27% |
| Structural Frame | 3222 | 9% | 32% |
| Masonry | 72 | 29% | 2% |
| MEP | 87 | 0% | 0% |
| Finishing | 1569 | 12% | 57% |
| External Works | 716 | 0% | — |
| Commissioning | 167 | 3% | 36% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation | 5 | 33 | 51 | 84 | 10 |  |  |  |  |  |
| Earthwork | 53 | 170 | 203 | 420 | 48 | 6 |  |  |  |  |
| Foundation | 33 | 28 | 133 | 1088 | 126 | 246 | 145 |  |  | 2 |
| Structural Frame | 6 | 6 | 55 | 1294 | 274 | 458 | 1057 | 72 |  |  |
| Masonry |  |  |  | 25 | 26 | 21 |  |  |  |  |
| MEP |  |  | 2 | 85 |  |  |  |  |  |  |
| Finishing | 71 | 13 | 17 | 569 | 319 | 198 | 186 | 196 |  |  |
| External Works |  | 53 | 91 | 483 | 35 | 4 | 13 | 30 |  | 7 |
| Commissioning |  |  |  | 45 | 19 | 51 | 1 | 46 |  | 5 |

# Модуль 3 на таймлапсах

Размеченных дней: 8717, роликов: 14. Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).

- точность: **19.8%**
- точность ±1 фаза по порядку: 51.6%
- средняя уверенность: 82%

## По роликам

| ролик | дней | точность | ±1 фаза | чаще всего предсказано |
|---|---|---|---|---|
| 15_Months_of_Construction_in_Just_60_Seconds_cinematic_4K_Time_Lapse.mp4 | 457 | 40% | 98% | Foundation 49%, Earthwork 30% |
| Construction_Documentary_in_4K_Timelapse_Laboratory_Office_Complex.mp4 | 900 | 20% | 51% | Foundation 45%, Masonry 23% |
| Construction_site_time_lapse_office_building_Munich_4K_SIMPLE_TIME.mp4 | 540 | 23% | 64% | Foundation 37%, MEP 36% |
| Erweiterung_Implerhöfe_München_Bau_Zeitraffer_get_save_com.mp4 | 450 | 19% | 86% | Foundation 86%, Earthwork 6% |
| German_construction_site_time_lapse_SIMPLE_TIME_LAPSE_4K_get_save.mp4 | 400 | 37% | 74% | Foundation 38%, Masonry 24% |
| Gymnasium_Georg_Zech_Allee_16_Pavillonanlage_Containerbau_Zeitraffer.mp4 | 240 | 25% | 58% | Foundation 68%, Structural Frame 10% |
| HIGH_RISE_Construction_timelapse_in_Frankfurt_4K_get_save_com.mp4 | 900 | 5% | 16% | MEP 42%, Masonry 32% |
| Healthcare_Construction_Timelapse_Building_a_Modern_Care_Facility.mp4 | 720 | 23% | 31% | Foundation 32%, MEP 24% |
| Office_Building_PANDION_RISE_Timelapse_Construction_Documentary.mp4 | 1000 | 11% | 35% | MEP 38%, Masonry 20% |
| Power_Plant_Timelapse_Battery_Storage_Facility_Site_Documentation.mp4 | 270 | 36% | 45% | Foundation 81%, Earthwork 9% |
| Radiation_Protection_Center_Construction_Timelapse_Nuclear_Safety.mp4 | 700 | 17% | 28% | Foundation 34%, Structural Frame 21% |
| Residential_Development_Construction_Timelapse_New_Sustainable_Neighborhood.mp4 | 540 | 27% | 69% | Foundation 60%, MEP 10% |
| Time_Lapse_Construction_OFFICEHOME_Soul_Munich_get_save_com.mp4 | 1000 | 12% | 49% | Masonry 33%, Foundation 30% |
| construction_site_time_lapse_residential_complex_Düsseldorf_Germany.mp4 | 600 | 27% | 87% | Foundation 64%, Structural Frame 17% |

## По фазам

| фаза | дней в эталоне | recall | precision |
|---|---|---|---|
| Preconstruction | 0 | — | 0% |
| Site Preparation | 183 | 20% | 12% |
| Earthwork | 900 | 24% | 40% |
| Foundation | 1801 | 52% | 27% |
| Structural Frame | 3222 | 12% | 39% |
| Masonry | 72 | 0% | 0% |
| MEP | 87 | 0% | 0% |
| Finishing | 1569 | 10% | 57% |
| External Works | 716 | 1% | 30% |
| Commissioning | 167 | 0% | 0% |

## Матрица ошибок (строки — эталон, столбцы — предсказание)

| | Preconstruction | Site Preparation | Earthwork | Foundation | Structural Frame | Masonry | MEP | Finishing | External Works | Commissioning |
|---|---|---|---|---|---|---|---|---|---|---|
| Preconstruction |  |  |  |  |  |  |  |  |  |  |
| Site Preparation | 6 | 37 | 72 | 58 | 10 |  |  |  |  |  |
| Earthwork | 67 | 168 | 219 | 386 | 57 | 2 | 1 |  |  |  |
| Foundation | 24 | 40 | 94 | 937 | 258 | 286 | 113 | 6 |  | 43 |
| Structural Frame | 5 | 4 | 16 | 790 | 371 | 766 | 1134 | 73 | 8 | 55 |
| Masonry |  |  | 5 | 27 | 40 |  |  |  |  |  |
| MEP |  |  | 2 | 85 |  |  |  |  |  |  |
| Finishing | 61 | 29 | 27 | 691 | 152 | 267 | 169 | 157 | 6 | 10 |
| External Works | 29 | 30 | 119 | 413 | 25 | 17 | 62 | 13 | 6 | 2 |
| Commissioning |  |  |  | 41 | 44 | 57 |  | 25 |  |  |

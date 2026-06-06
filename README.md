# 🧠 Mente y Emoción en UX

[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![Open Science](https://img.shields.io/badge/Open%20Science-Data%20%26%20Code-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Repositorio oficial del proyecto de investigación para la obtención del título de **Ingeniería en Computación Inteligente** por la **Universidad Autónoma de Aguascalientes (UAA)**.

**Autor:** José Luis Sandoval Pérez  


---

## 📌 Descripción del Proyecto

Este proyecto propone un marco de evaluación cuantitativo para la Interacción Humano-Computadora (HCI), triangulando métricas psicométricas estandarizadas (NASA-TLX, SUS) con datos neurofisiológicos continuos (EEG). 

El sistema utiliza una diadema **NeuroSky MindWave Mobile 2** para capturar la actividad del lóbulo frontal (electrodo Fp1) y calcular el **Índice de Carga Cognitiva (CLI = Theta / Alfa)** en tiempo real. Esto permite auditar, milisegundo a milisegundo, la fricción cognitiva y la frustración que experimenta un usuario al interactuar con interfaces web, demostrando cómo el *Diseño Emocional* puede actuar como un amortiguador cognitivo e inducir un estado de inmersión (*Flow State*).

## 🗂️ Estructura del Repositorio

El proyecto está modularizado en tres fases principales:

* `📁 1_captura/`
  Contiene los scripts de Python de la arquitectura concurrente. 
  * `mindwave.py`: Capa de comunicación Bluetooth para decodificación de paquetes del hardware.
  * `grabadora.py`: Implementación de hilos concurrentes para la captura, procesamiento espectral y almacenamiento en crudo.

* `📁 2_analisis_individual/`
  Colección de cuadernos de Jupyter (`.ipynb`) separados por participante (P1 - P5). Aquí se implementa:
  * Filtro pasa-banda Butterworth (fase cero).
  * Transformada Rápida de Fourier (FFT) y enventanado Hann.
  * Cálculo de series temporales suavizadas con media móvil de 10 segundos.

* `📁 3_datasets/`
  Conjuntos de datos experimentales recopilados durante la investigación. Contiene los CSV de la señal electroencefalográfica y las respuestas tabuladas de los instrumentos psicométricos.



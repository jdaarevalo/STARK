blueprint exacto para tu interfaz gráfica en Streamlit, sección por sección:

1. El HUD Lateral (Sidebar): S.T.A.R.K. Daily Readiness
Esta sección es tu semáforo diario. Responde a: ¿Cómo está el chasis hoy?

Información: Tu estado de recuperación instantáneo de esa mañana.

Variables (DuckDB/Garmin): * HRV actual vs. Promedio de 7 días.

Resting Heart Rate (RHR) vs. Promedio de 7 días.

Body Battery (valor al despertar).

Sleep Score (0-100).

Soreness (Input manual del 1 al 10).

Tipo de Gráfico: Tarjetas de KPI (Key Performance Indicators).

Números grandes y limpios.

Usar deltas (flechas rojas/verdes). Ejemplo: Si el HRV promedio es 45 y hoy tienes 38, el delta debe estar en rojo (↓ 7ms). Si el dolor (Soreness) es > 5, toda la tarjeta debe tener un borde rojo de alerta.

2. Módulo Principal Superior Izquierdo: Load Balance (Gestión de Carga)
El módulo más importante para prevenir lesiones. Responde a: ¿Me estoy sobreentrenando?

Información: Compara tu fatiga reciente con tu condición física acumulada.

Variables (DuckDB): Necesitas calcular 3 variables a partir de los RunSummary.

ATL (Acute Training Load): Promedio móvil exponencial de la carga de los últimos 7 días. (La fatiga).

CTL (Chronic Training Load): Promedio de los últimos 42 días. (Tu fitness real).

TSB (Training Stress Balance): La resta matemática CTL - ATL.

Tipo de Gráfico: Gráfico de Líneas Dual con Área Sombreada (Dual Line with Area).

Eje X: Fecha (últimos 30 días).

Eje Y: Valor de carga.

Línea Azul: CTL (Condición).

Línea Naranja/Roja: ATL (Fatiga).

Área inferior: TSB. Si el área es negativa (roja), estás acumulando demasiada fatiga. Si es positiva (verde), estás listo para competir.

3. Módulo Principal Superior Derecho: Intensity Distribution (Motor Aeróbico)
El validador de tu estrategia. Responde a: ¿Estoy respetando la regla 80/20?

Información: Cuánto tiempo pasas corriendo lento vs. corriendo rápido. Los corredores amateurs suelen fracasar en la media maratón porque corren sus entrenamientos suaves demasiado rápido.

Variables (DuckDB/Garmin):

Suma de minutos semanales agrupados por Zonas de Frecuencia Cardíaca (Zone 1-2, Zone 3, Zone 4-5).

Tipo de Gráfico: Gráfico de Barras Horizontales Apiladas al 100% (100% Stacked Bar Chart).

Una sola barra gruesa que represente la semana actual (y debajo la semana anterior para comparar).

Color Verde (Zona 1/2), Color Amarillo (Zona 3), Color Rojo (Zona 4/5).

Detalle visual crítico: Añade una línea vertical punteada exactamente en la marca del 80%. Visualmente, la franja verde debe llegar hasta esa línea.

4. Módulo Inferior Izquierdo: Run Efficiency (Evolución de Rendimiento)
El medidor de progreso. Responde a: ¿Soy más rápido con el mismo esfuerzo?

Información: Relación entre tu corazón y tus piernas. A medida que tu "Arc Reactor" se vuelve más eficiente, deberías correr más rápido a las mismas pulsaciones.

Variables (DuckDB): * Grade Adjusted Pace (Ritmo ajustado por pendiente - en min/km).

Average Heart Rate (Frecuencia cardíaca media).

Filtro esencial: Este gráfico solo debe alimentarse de las carreras etiquetadas como "Zona 2 / Base", excluyendo las series de velocidad.

Tipo de Gráfico: Gráfico de Líneas con Doble Eje Y (Dual-Axis Line Chart).

Eje X: Semanas.

Eje Y Izquierdo (Ritmo): Invertido (los ritmos más rápidos van hacia arriba).

Eje Y Derecho (Pulsaciones).

A lo largo de los meses, deberías ver las pulsaciones mantenerse planas, pero la línea de ritmo subiendo.

5. Módulo Inferior Derecho: Mission Status & Gear (Logística)
El control de la misión. Responde a: ¿Cuánto falta y en qué estado está mi armadura?

Información: Días para la carrera, ritmo objetivo y estado del equipamiento (zapatillas) para evitar roturas de material.

Variables:

Fecha actual vs. target_race_date.

Ritmo objetivo de carrera (ej. 4:55 min/km).

current_shoes_mileage (Kilometraje acumulado haciendo un SUM de las carreras hechas con cada zapatilla).

Tipo de Gráfico: Medidores (Gauges/Donut Charts) y Texto.

Texto grande: "34 Días para la Media Maratón".

Texto medio: "Target Pace: 4:55 /km".

Gráficos Gauge tipo progreso circular: Zapatilla A (verde si está < 400km, amarillo si está en 500km, rojo +600km).
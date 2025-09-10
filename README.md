# SynapseMD

SynapseMD es un editor de Markdown inteligente y potente, diseñado para potenciar tu escritura con la ayuda de la inteligencia artificial. Creado con Streamlit, ofrece una interfaz de usuario fluida y amigable que se integra perfectamente con avanzados modelos de lenguaje (LLMs) para ayudarte a crear y editar documentos de manera más eficiente.

Ya sea que estés redactando un informe, escribiendo un artículo o tomando notas, SynapseMD actúa como tu asistente de escritura personal, permitiéndote aprovechar el poder de tus propios documentos como contexto para generar contenido relevante y coherente.

## Características Principales

SynapseMD está repleto de funcionalidades diseñadas para mejorar cada etapa del proceso de escritura:

  * **Editor de Markdown Completo:** Una interfaz limpia y sencilla para escribir y dar formato a tus documentos usando la sintaxis Markdown.
  * **Gestión de Borradores:** Guarda, carga, renombra y elimina tus borradores fácilmente. Todo el trabajo se almacena de forma segura y organizada por usuario.
  * **Asistencia por IA:**
      * **Generación de Borrador Inicial:** Proporciona unas simples instrucciones y deja que la IA genere un borrador completo para ti, utilizando el contexto que le proporciones.
      * **Sugerencias de Autocompletado:** Obtén sugerencias inteligentes en tiempo real para continuar tus frases y párrafos.
      * **Ideas para Nuevas Secciones:** La IA puede analizar tu borrador actual y el contexto seleccionado para sugerir los siguientes pasos lógicos, proponiendo títulos y contenido para nuevas secciones.
  * **Gestión de Contexto Inteligente:**
      * **Sube tus Fuentes:** Añade archivos PDF o pega texto plano para crear una base de conocimiento personalizada.
      * **Extracción de Texto Avanzada:** El sistema extrae automáticamente el texto de los PDFs, utilizando tecnología OCR (Reconocimiento Óptico de Caracteres) para documentos escaneados.
      * **Resúmenes Automáticos:** Para que puedas referenciar rápidamente el contenido de tus documentos, la IA genera resúmenes concisos de cada página.
  * **Soporte Multi-Backend para LLMs:**
      * **Google Gemini:** Conéctate fácilmente a los potentes modelos de Google a través de su API.
      * **LM Studio:** Utiliza modelos de lenguaje que se ejecutan localmente en tu máquina a través de LM Studio para un control total y privacidad.
  * **Autenticación y Almacenamiento Seguro:**
      * **Inicio de Sesión con Google:** Accede de forma segura con tu cuenta de Google.
      * **Espacio de Trabajo Personal:** Cada usuario tiene su propio directorio privado para almacenar borradores y fuentes de contexto, garantizando la privacidad de la información.

## Cómo Funciona

SynapseMD combina un frontend interactivo construido con **Streamlit** con un backend modular en Python que gestiona la lógica de la aplicación.

1.  **Interfaz de Usuario (`synapse_main.py`):** Es el punto de entrada de la aplicación. Gestiona la interfaz, las pestañas del editor y la gestión de contexto, y orquesta las llamadas a los otros módulos.
2.  **Autenticación (`auth_helpers.py`):** Maneja el inicio de sesión del usuario y la información de la sesión para garantizar un acceso seguro.
3.  **Procesamiento de Contexto (`context_processing.py`):** Cuando un usuario sube un archivo (ej. un PDF), este módulo se encarga de procesarlo. Utiliza **PyMuPDF** para la extracción de texto y **EasyOCR** para las imágenes, para después generar y guardar resúmenes utilizando un LLM.
4.  **Funciones del Editor (`editor_features.py`):** Contiene toda la lógica de la IA para la escritura. Se comunica con el módulo `llm_interface.py` para generar borradores, sugerencias y autocompletados.
5.  **Interfaz de LLM (`llm_interface.py`):** Actúa como una capa de abstracción que permite a la aplicación comunicarse con diferentes backends de IA (Google Gemini o LM Studio) de manera intercambiable.
6.  **Almacenamiento (`storage.py`):** Gestiona toda la interacción con el sistema de archivos, guardando y recuperando borradores y datos de contexto en una estructura de carpetas organizada por usuario.

## Instalación y Puesta en Marcha

Sigue estos pasos para ejecutar SynapseMD en tu entorno local.

### Prerrequisitos

  * Python 3.8 o superior.
  * Git.

### Pasos

1.  **Clona el repositorio:**

    ```bash
    git clone https://github.com/ici-laboratories/synapse-md.git
    cd synapse-md
    ```

2.  **Crea y activa un entorno virtual:**

    ```bash
    python -m venv venv
    # En Windows
    venv\Scripts\activate
    # En macOS/Linux
    source venv/bin/activate
    ```

3.  **Instala las dependencias:**
    Crea un archivo `requirements.txt` con el siguiente contenido:

    ```
    streamlit
    google-generativeai
    PyMuPDF
    easyocr
    pillow
    requests
    ```

    Y luego instálalo:

    ```bash
    pip install -r requirements.txt
    ```

4.  **Configura tus secretos:**
    Crea un archivo en `.streamlit/secrets.toml` y añade tu clave de API de Google Gemini:

    ```toml
    GEMINI_API_KEY = "TU_API_KEY_DE_GOOGLE_AQUI"
    ```

    Si no configuras la clave, la aplicación te advertirá al iniciar.

5.  **Ejecuta la aplicación:**

    ```bash
    streamlit run synapse_main.py
    ```

Abre tu navegador en la dirección local que te indique Streamlit y empieza a escribir.

## Estructura del Proyecto

```
/
├── .streamlit/
│   └── secrets.toml
├── synapse_main.py
├── editor_features.py
├── context_processing.py
├── storage.py
├── llm_interface.py
├── config.py
├── auth_helpers.py
├── .gitignore
└── README.md
```

## Créditos

Este proyecto fue creado por estudiantes de la **Universidad de Colima**, de la **Facultad de Ingeniería Mecánica y Eléctrica** en la carrera de **Ingeniería en Computación Inteligente**.

  * Oliver Sanchez Corona
  * Pedro Antonio Ibarra Facio
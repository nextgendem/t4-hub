from flask import Flask, request, redirect, session
import requests
import os

app = Flask(__name__)
app.secret_key = 'tGYE7TPEmOdGlGZYwfXiCtjvy7mfUPAPRbwwdhUkd2w='

# Configuración de variables de entorno
GOOGLE_CLIENT_ID = '301448114319-lpnvs5o0pdf5qcptkbmilpitr81qh8vt.apps.googleusercontent.com'
GOOGLE_CLIENT_SECRET = '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'
REDIRECT_URI = 'http://localhost:5000/oauth2/callback'  # Cambia según sea necesario

@app.route('/oauth2/callback')
def callback():
    # Obtén el código de autorización
    code = request.args.get('code')

    # Intercambia el código por un token de acceso
    token_response = requests.post(
        'https://oauth2.googleapis.com/token',
        data={
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI,
            'grant_type': 'authorization_code',
        },
    )

    # Verifica si la solicitud fue exitosa
    if token_response.status_code != 200:
        return 'Error en la obtención del token', 400

    # Almacena el token en la sesión
    token_data = token_response.json()
    session['token'] = token_data['access_token']

    # Opcional: Obtener información del usuario
    user_info_response = requests.get(
        'https://www.googleapis.com/oauth2/v1/userinfo',
        headers={'Authorization': f'Bearer {session["token"]}'}
    )
    
    if user_info_response.status_code == 200:
        user_info = user_info_response.json()
        session['user'] = user_info

    return redirect("http://localhost:6901")  # Redirige a donde necesites

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)  # Escucha en el puerto 5000

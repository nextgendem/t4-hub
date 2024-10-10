# T4-Hub (O2auth and intermediate)
Sessions division with Google Authentication and redirection with Python Container (Flask)
<details>
 <summary>Preinstallation:</summary>

  
1. Follow https://github.com/nextgendem/t4-novnc
- Note: if isn't working, change src/common/install/kasm_vnc/www/package.json with
  package.json /fix folder.
  
2. Clone oauth2 repository.
  ```bash
  git clone  https://github.com/oauth2-proxy/oauth2-prox
  ```

3. Build repository with <code style="color : red">o2-auth/o2-auth</code> tag
  ```bash
  cd src
  docker build -t o2-auth/o2-auth .
  ```
  
</details>

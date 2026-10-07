/* Brain visualization — Three.js 3D brain with active regions. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const regions = [
    { name: "Cerebrum", color: 0x3fb950 },
    { name: "Cerebellum", color: 0x3fb950 },
    { name: "Brainstem", color: 0x3fb950 },
    { name: "Thalamus", color: 0x3fb950 },
    { name: "Hippocampus", color: 0x3fb950 },
    { name: "Amygdala", color: 0x3fb950 },
    { name: "Prefrontal Cortex", color: 0x3fb950 },
    { name: "Hypothalamus", color: 0x3fb950 },
    { name: "Occipital Lobe", color: 0x3fb950 },
    { name: "Parietal Lobe", color: 0x3fb950 },
    { name: "Temporal Lobe", color: 0x3fb950 },
    { name: "Frontal Lobe", color: 0x3fb950 },
    { name: "Corpus Callosum", color: 0x3fb950 },
    { name: "Basal Ganglia", color: 0x3fb950 },
    { name: "Spinal Cord", color: 0xd29922 },
    { name: "Reticular Formation", color: 0x3fb950 },
    { name: "Broca's Area", color: 0x3fb950 },
    { name: "Wernicke's Area", color: 0x3fb950 },
    { name: "Mirror Neurons", color: 0x3fb950 },
  ];

  let scene, camera, renderer, meshes = [];
  let activeRegion = null;

  function init() {
    const container = $("brain-container");
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(60, container.clientWidth / Math.max(1, container.clientHeight), 0.1, 1000);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambient);
    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(5, 10, 7);
    scene.add(dir);

    const geometry = new THREE.SphereGeometry(5, 32, 32);
    const baseMat = new THREE.MeshPhongMaterial({ color: 0x161b22, transparent: true, opacity: 0.9 });
    const base = new THREE.Mesh(geometry, baseMat);
    scene.add(base);

    regions.forEach(function (r, i) {
      const phi = Math.acos(-1 + (2 * i + 1) / regions.length);
      const theta = Math.sqrt(regions.length * Math.PI) * phi;
      const r_radius = 5.2;
      const x = r_radius * Math.cos(theta) * Math.sin(phi);
      const y = r_radius * Math.sin(theta) * Math.sin(phi);
      const z = r_radius * Math.cos(phi);
      const dotGeo = new THREE.SphereGeometry(0.25, 16, 16);
      const dotMat = new THREE.MeshPhongMaterial({ color: r.color, emissive: r.color, emissiveIntensity: 0.3 });
      const dot = new THREE.Mesh(dotGeo, dotMat);
      dot.position.set(x, y, z);
      dot.userData = { name: r.name, baseColor: r.color };
      scene.add(dot);
      meshes.push(dot);
    });

    camera.position.z = 14;

    let dragging = false;
    let prevX = 0, prevY = 0;
    renderer.domElement.addEventListener("pointerdown", function (e) {
      dragging = true;
      prevX = e.clientX;
      prevY = e.clientY;
    });
    renderer.domElement.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      const dx = e.clientX - prevX;
      const dy = e.clientY - prevY;
      scene.rotation.y += dx * 0.005;
      scene.rotation.x += dy * 0.005;
      prevX = e.clientX;
      prevY = e.clientY;
    });
    renderer.domElement.addEventListener("pointerup", function () {
      dragging = false;
    });

    renderer.domElement.addEventListener("click", function (e) {
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new THREE.Vector2();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(meshes);
      if (hits.length) {
        const obj = hits[0].object;
        showOverlay(obj.userData.name);
      }
    });

    window.addEventListener("resize", function () {
      camera.aspect = container.clientWidth / Math.max(1, container.clientHeight);
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    });

    animate();
  }

  function showOverlay(name) {
    const overlay = $("brain-overlay");
    $("overlay-title").textContent = name;
    $("overlay-detail").textContent = "Region status: healthy";
    overlay.classList.remove("hidden");
  }

  function animate() {
    requestAnimationFrame(animate);
    meshes.forEach(function (m, i) {
      m.material.emissiveIntensity = 0.3 + 0.2 * Math.sin(Date.now() * 0.002 + i);
    });
    renderer.render(scene, camera);
  }

  $("overlay-close").addEventListener("click", function () {
    $("brain-overlay").classList.add("hidden");
  });

  document.addEventListener("DOMContentLoaded", init);
})(window);

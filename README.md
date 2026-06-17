<!-- PROJECT SHIELDS -->
[![Contributors][contributors-shield]](https://github.com/rhysdg/ollama-voice-jetson/contributors)
[![Apache][license-shield]][license-url]
[![LinkedIn][linkedin-shield]][linkedin-url]

<!-- PROJECT LOGO -->
<br />
  <h3 align="center"> Accelerated Vector</h2>
  <p align="center">
     A wire-pod enabled 3D animation and accelerated machine learning suite for Vector
     <br />
    <a href="https://github.com/rhysdg/accelerated-vector/wiki"<strong>Explore the docs »</strong></a>
    <br />
    <br />
    <img src="images/accelerated-vector.gif" align="middle" width=600>
    <br />
    <br />
    <a href="https://github.com/rhysdg/accelerated-vector/issues">Report Bug</a>
    .
    <a href="https://github.com/rhysdg/accelerated-vector/issues">Request Feature</a>
  </p>
</p>

<!-- TABLE OF CONTENTS -->
## Table of Contents

* [About the Project](#about-the-project)
  * [Built With](#built-with)
  * [The Story so Far](#the-story-so-far)
* [Getting Started](#getting-started)
  * [Prerequisites](#prerequisites)
  * [Scripts and Tools](#scripts-and-tools)
  * [Supplementary Data](#supplementary-data)
* [Proposed Updates](#proposed-updates)
* [Contact](#contact)

<!-- ABOUT THE PROJECT -->
## About The Project

### Built With

* [Blender 4](https://www.blender.org/)
* [Onnxruntime](https://onnxruntime.ai/)


### The Story So Far

**Coming soon**

In the meantime check out the ongoing youtube series here:

[![Everything Is AWESOME](https://i.ytimg.com/vi/fHoTQWiJFe0/hqdefault.jpg?sqp=-oaymwE2CPYBEIoBSFXyq4qpAygIARUAAIhCGAFwAcABBvABAfgB_gmAAtAFigIMCAAQARhlIGUoZTAP&rs=AOn4CLAu9E-D9Esj_6qKSqrrpJXg9vi36g)](https://www.youtube.com/watch?v=OQMk-K9NM3w&list=PLhjBVq157J6p6ea1Z1D-D8fh4uDna5mnl "Everything Is AWESOME")

<!-- INSTALLATION -->
## Installation

The Blender addon lives in the `vector_animation/` directory. Two ways to install:

### Option 1: Symlink (development)

Link the addon folder into Blender's user addons directory so changes are picked up immediately:

```bash
# Blender 5.x (adjust version number as needed)
mkdir -p ~/.config/blender/5.1/scripts/addons
ln -s /absolute/path/to/accelerated-vector/vector_animation \
      ~/.config/blender/5.1/scripts/addons/vector_animation
```

Then in Blender: **Edit → Preferences → Add-ons** → search for *"Servo"* → enable the checkbox.

After editing any addon file, reload scripts with <kbd>F3</kbd> → *"Reload Scripts"* or disable/re-enable the addon in Preferences.

### Option 2: Zip (distribution)

Zip the addon folder and install through Blender's Preferences:

```bash
cd /path/to/accelerated-vector
zip -r vector_animation.zip vector_animation/
```

Then in Blender: **Edit → Preferences → Add-ons → Install from Disk…** → select `vector_animation.zip` → enable the checkbox.

<!-- GETTING STARTED -->
## Getting Started:

**coming soon**

  
## Customisation:

- **Coming soon**


### Notebooks


1. **Coming soon**


### Testing

 - CI/CD will be expanded as we go - all general instantiation tests pass so far.

### Models & Latency benchmarks




### Similar projects

- Pending

<!-- PROPOSED UPDATES -->
## Latest Updates

**coming soon**

<!-- PROPOSED UPDATES -->
## Future updates
- facial expressions and sound dropdown during animation
- onnxruntime based accelereated machine learning.
-live cam with ML plugin

<!-- Contact -->
## Contact
- Project link: https://github.com/rhysdg/accelerated-vector
- Email: [Rhys](rhysdgwilliams@gmail.com)


<!-- MARKDOWN LINKS & IMAGES -->
[build-shield]: https://img.shields.io/badge/build-passing-brightgreen.svg?style=flat-square
[contributors-shield]: https://img.shields.io/badge/contributors-2-orange
[license-shield]: https://img.shields.io/badge/License-GNU%20GPL-blue
[license-url]: LICENSE.txt
[linkedin-shield]: https://img.shields.io/badge/-LinkedIn-black.svg?style=flat-square&logo=linkedin&colorB=555
[linkedin-url]: https://www.linkedin.com/in/rhys-williams-b19472160/

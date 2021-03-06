#!/bin/bash

set -e

echo "This script will run the RAID1 bootstrap script and install Arch Linux."
echo
echo "Press enter to start..."
read

echo -n '[?] Type new system hostname (e.g. gw or rhfsA): ' && read hostname
echo -n '[?] Type new system root password: ' && read -s root_password
echo
echo -n '[?] Confirm new system root password: ' && read -s root_password_cnf
echo

if [ "$root_password" != "$root_password_cnf" ]; then
    >&2 echo "Passwords mismatch, aborting."
    exit 1
fi

echo '[+] Detected disks:'
shopt -s nullglob  # glob pattern is removed if it has no matches
for disk in /dev/vd? /dev/sd?; do
    fdisk -l ${disk} | grep Disk | grep bytes
done
while ! [ -b "$disk_0" ]; do
    echo -n '[?] First disk (boot drive): ' && read disk_0
done
while ! [ -b "$disk_1" ]; do
    echo -n '[?] Second disk (RAID slave): ' && read disk_1
done

echo "[+] Starting RAID1 bootstrap"
./bootstrap_fs_raid1.sh "$hostname" "$disk_0" "$disk_1"

echo "[+] Starting Arch Linux bootstrap"
./bootstrap_arch_linux.sh /mnt $hostname.prolo <(echo "$root_password")

echo "[+] Starting post RAID1 bootstrap"
./bootstrap_fs_raid1_post.sh $hostname

echo "[+] You can reboot now!"
